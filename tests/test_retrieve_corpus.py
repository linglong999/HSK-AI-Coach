# ============================================================
# M7 retrieve_corpus 技能 · 集成测试（0.18 接入项②）
# 覆盖（agent-design M7 验收标准）：
#   - "把字句是啥"返回含正确答案的 top-k 片段 + 来源（防编造）
#   - 偏误图谱入索引：该生历史错法可检索（M8 画像联动）
#   - 图谱新鲜度：对话中新增偏误后可检索（幂等 re-ingest）
#   - 共享索引：lookup_knowledge_point 与 retrieve_corpus 复用同一实例
#   - planner 自主调度：mock LLM 调 retrieve_corpus → used_skills 命中
# 运行: python -m unittest tests.test_retrieve_corpus -v
# ============================================================

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.graph.error_graph import ErrorGraph, Node
from skills import build_registry
from skills.retrieve_corpus import RetrieveCorpusSkill
from planner.loop import Planner, is_tool_result_message


def _make_graph(learner_id="m7skill"):
    g = ErrorGraph(learner_id=learner_id)
    g.ingest_error({
        "fragment": "把手", "type": "语法", "type_confident": True,
        "confidence": 0.9, "knowledge_point_id": "kp-custom-ta",
        "knowledge_point_name": "他她混淆", "level": "HSK2",
    }, event_key="e1")
    return g


class RetrieveCorpusSkillTest(unittest.TestCase):
    """技能层：top-k + 来源 + 过滤 + 入参校验。"""

    def setUp(self):
        self.graph = _make_graph()
        self.skill = RetrieveCorpusSkill(graph=self.graph)

    def test_kp_query_returns_sourced_hits(self):
        # 验收①：知识性问题 → 含正确答案的 top-k 片段 + 来源
        out = self.skill.run({"query": "把字句是啥"})
        self.assertGreater(out["count"], 0)
        first = out["results"][0]
        self.assertEqual(first["kp_id"], "kp-ba-sentence")
        self.assertEqual(first["source"], "knowledge_point")
        for field in ("chunk_id", "source", "kp_id", "knowledge_point",
                      "level", "text", "score"):
            self.assertIn(field, first)   # 来源字段齐备（引用防编造）

    def test_graph_source_retrieval(self):
        # 验收②：该生历史错法（图谱自建节点）可检索，source=graph
        out = self.skill.run({"query": "他她混淆"})
        sources = {r["source"] for r in out["results"]}
        hit = [r for r in out["results"] if r["chunk_id"] == "kp-custom-ta"]
        self.assertTrue(hit)
        self.assertEqual(hit[0]["source"], "graph")
        self.assertTrue(hit[0]["in_graph"])
        self.assertIn("graph", sources)

    def test_graph_freshness_reingest(self):
        # 新鲜度：首轮检索（索引已建）→ 图谱新增节点 → 再检可命中（幂等 re-ingest）
        self.skill.run({"query": "把字句"})
        self.graph._nodes["kp-late-xyz"] = Node(
            id="kp-late-xyz", knowledge_point="迟到补测点", level="HSK1")
        # 直接塞 _nodes 不走 ingest；用 review queue 暴露它（ingest_graph 读 queue）
        # ErrorGraph.get_review_queue 基于 _nodes 计算 → 无需额外操作
        out = self.skill.run({"query": "迟到补测点"})
        ids = [r["chunk_id"] for r in out["results"]]
        self.assertIn("kp-late-xyz", ids)

    def test_top_k_clamp(self):
        out = self.skill.run({"query": "把", "top_k": 2})
        self.assertLessEqual(out["count"], 2)
        out = self.skill.run({"query": "把", "top_k": 99})
        self.assertLessEqual(out["count"], 10)   # 上限 10
        out = self.skill.run({"query": "把", "top_k": "bad"})
        self.assertGreaterEqual(out["count"], 1)  # 非法回退默认 5

    def test_kp_only_filter(self):
        out = self.skill.run({"query": "他她混淆", "kp_only": True})
        self.assertTrue(out["results"])
        self.assertTrue(all(r["source"] == "knowledge_point"
                            for r in out["results"]))

    def test_level_normalize(self):
        # 'HSK3' / '3' / 3 归一到 3（KB 与词表 chunk 均存 int 等级）
        out = self.skill.run({"query": "把", "level": "HSK3"})
        self.assertTrue(out["results"])
        self.assertTrue(all(int(r["level"]) == 3 for r in out["results"]))
        out = self.skill.run({"query": "把", "level": "4"})
        self.assertTrue(all(int(r["level"]) == 4 for r in out["results"]))

    def test_empty_query_error(self):
        out = self.skill.run({"query": "  "})
        self.assertEqual(out["count"], 0)
        self.assertIn("_error", out)

    def test_miss_returns_error_not_fabricated(self):
        out = self.skill.run({"query": "夔夔夔"})
        self.assertEqual(out["count"], 0)
        self.assertIn("_error", out)   # 未命中如实报，不编造


class SharedIndexTest(unittest.TestCase):
    """装配层：build_registry 共享索引 + 9 技能注册 + lookup 不回归。"""

    def test_registry_has_nine_skills(self):
        reg = build_registry()
        self.assertEqual(reg.count(), 9)
        self.assertTrue(reg.has("retrieve_corpus"))

    def test_two_skills_share_one_index(self):
        reg = build_registry()
        lookup = reg.get("lookup_knowledge_point")
        retrieve = reg.get("retrieve_corpus")
        self.assertIs(lookup._rag, retrieve._rag)   # 同一实例（避免重复构建）

    def test_lookup_still_works_with_shared_index(self):
        # 共享索引含 graph 源，但 lookup 的 kp_only 查询不受影响
        reg = build_registry()
        out = reg.get("lookup_knowledge_point").run({"keyword": "把字句"})
        self.assertGreater(out["count"], 0)
        self.assertEqual(out["results"][0]["id"], "kp-ba-sentence")
        self.assertTrue(all(r["source"] == "knowledge_point"
                            for r in out["results"]))


class PlannerDispatchTest(unittest.TestCase):
    """planner 集成：知识性问题 → 自主调 retrieve_corpus → 引用来源作答。"""

    def test_planner_calls_retrieve_corpus(self):
        reg = build_registry()

        def mock_llm(messages):
            if not is_tool_result_message(messages[-1]):
                return json.dumps([
                    {"type": "action", "name": "retrieve_corpus",
                     "params": {"query": "把字句", "top_k": 3}},
                    {"type": "text", "content": "我先查一下权威语料。"},
                ], ensure_ascii=False)
            tool = json.loads(messages[-1]["content"].removeprefix("[tool_result] "))
            kp = tool["result"]["results"][0]["knowledge_point"]
            return json.dumps(
                [{"type": "text",
                  "content": f"根据知识点清单（来源：{kp}），把字句是'把+宾语+动词+补语'结构。"}],
                ensure_ascii=False)

        p = Planner(reg, llm_call=mock_llm)
        r = p.run("把字句是啥")
        self.assertFalse(r["fallback"])
        self.assertIn("retrieve_corpus", r["used_skills"])
        self.assertIn("知识点清单", r["text"])
        # trace 携带来源字段（前端学习成果卡可渲染参考来源）
        tr = r["trace"][0]
        self.assertTrue(tr["ok"])
        self.assertTrue(tr["result"]["results"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

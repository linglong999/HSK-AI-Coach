# ============================================================
# M7 RAG 检索增强 · 回归测试（tests/test_rag.py）
# 覆盖：倒排检索 / 术语成词 / 打分加权 / in_graph / 过滤 / 版本 / 空查询
# ============================================================

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.rag import HSKLexicalIndex, _tokenize, build_hsk_index
from engine.graph.error_graph import ErrorGraph


class TestTokenize(unittest.TestCase):
    def test_single_char_and_term(self):
        # “把字句”应作为整术语命中，同时保留单字
        toks = _tokenize("把字句")
        self.assertIn("把字句", toks)   # 术语成词（决策 B）
        self.assertIn("把", toks)
        self.assertIn("字", toks)

    def test_latin_and_digit(self):
        self.assertIn("HSK3", _tokenize("HSK3"))   # 连续字母数字为一组

    def test_empty(self):
        self.assertEqual(_tokenize(""), [])


class TestLexicalIndex(unittest.TestCase):
    def setUp(self):
        self.idx = HSKLexicalIndex()
        self.idx.ingest_knowledge_points()

    def test_kp_terms_hit(self):
        hits = self.idx.query("把字句", kp_only=True)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["chunk"]["kp_id"], "kp-ba-sentence")

    def test_liangci_term_not_split(self):
        # 量词不能因单字拆散而丢失；其次应命中 kp-liangci 且位居前列
        hits = self.idx.query("量词是什么", kp_only=True, top_k=6)
        ids = [h["chunk"]["kp_id"] for h in hits]
        self.assertIn("kp-liangci", ids)
        self.assertLess(ids.index("kp-liangci"), 3)

    def test_compare_sentence_hit(self):
        hits = self.idx.query("比较句", kp_only=True)
        self.assertIn("kp-bi-sentence", [h["chunk"]["kp_id"] for h in hits])

    def test_empty_query(self):
        self.assertEqual(self.idx.query(""), [])

    def test_level_filter(self):
        all_hits = self.idx.query("把", kp_only=True)
        lvl3 = self.idx.query("把", kp_only=True, level=3)
        self.assertLess(len(lvl3), len(all_hits))   # 过滤生效

    def test_version_changes(self):
        v1 = self.idx.compute_version()
        self.idx.ingest_lexicon()
        v2 = self.idx.compute_version()
        self.assertNotEqual(v1, v2)  # 源增减 → 版本变化（供重建判断）


class TestGraphIngest(unittest.TestCase):
    def test_no_graph_ok(self):
        idx = build_hsk_index(include_lexicon=False)  # 无 graph，不报错
        self.assertGreater(len(idx), 0)

    def test_graph_in_graph_marker(self):
        g = ErrorGraph(learner_id="m7test")
        # 构造一个已确认偏误 → 入队列/节点
        g.ingest_error({
            "fragment": "把手", "type": "语法", "type_confident": True,
            "confidence": 0.9, "knowledge_point_id": "kp-ba-sentence",
            "knowledge_point_name": "把字句", "level": "HSK3",
        }, event_key="e1")

        idx = HSKLexicalIndex()
        idx.ingest_knowledge_points()
        idx.ingest_graph(g)
        # kp-ba-sentence 已由知识清单覆盖 → in_graph 被补真
        chunk = idx.get_chunk("kp-ba-sentence")
        self.assertTrue(chunk["in_graph"])

    def test_graph_only_node(self):
        g = ErrorGraph(learner_id="m7test2")
        # 自建一个不在清单里的 KP 节点（走 graph 源）
        from engine.graph.error_graph import Node
        n = Node(id="kp-custom-xyz", knowledge_point="自定义点", level="HSK2")
        g._nodes["kp-custom-xyz"] = n
        idx = HSKLexicalIndex()
        idx.ingest_knowledge_points()
        idx.ingest_graph(g)
        self.assertIn("kp-custom-xyz", idx._chunks)


class TestSkillThroughRag(unittest.TestCase):
    def test_lookup_skill_returns_rag_hit(self):
        from skills.lookup_knowledge_point import LookupKnowledgePointSkill
        skill = LookupKnowledgePointSkill()
        out = skill.run({"keyword": "把字句"})
        self.assertGreater(out["count"], 0)
        first = out["results"][0]
        self.assertEqual(first["id"], "kp-ba-sentence")
        self.assertIn("score", first)       # RAG 增强字段
        self.assertIn("source", first)

    def test_lookup_kp_id_exact(self):
        from skills.lookup_knowledge_point import LookupKnowledgePointSkill
        skill = LookupKnowledgePointSkill()
        out = skill.run({"kp_id": "kp-ba-sentence"})
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["results"][0]["id"], "kp-ba-sentence")

    def test_lookup_miss_error(self):
        from skills.lookup_knowledge_point import LookupKnowledgePointSkill
        skill = LookupKnowledgePointSkill()
        # 用完全无交集字（避开常见字/术语），RAG 与子串兜底均不命中
        out = skill.run({"keyword": "夔夔夔"})
        self.assertEqual(out["count"], 0)
        self.assertIn("_error", out)


if __name__ == "__main__":
    unittest.main()
# ============================================================
# tests/test_generator.py
# 生成引擎全流程（0.16）验收：authoring→LLM→校验→自愈重试→写回
# 覆盖 5 条验收标准。纯标准库 mock 注入，无 Key 环境也不崩。
# ============================================================

import os
import sys
import unittest
from unittest.mock import MagicMock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.generation.generator import GenerationEngine
from engine.generation.validator import GenerationUnitValidator
from engine.graph.error_graph import ErrorGraph
from engine.graph.error_graph import Node
from engine.llm.client import JSONStrictError
from engine.memory.writeback import Writeback


def valid_explain() -> dict:
    return {
        "id": "u-3", "type": "explain", "title": "量词只",
        "keyPoints": ["量词'只'用于个体量词"],
        "forbidden_errors": [],
        "context": {"curriculumAt": "kp:只"},
        "self_language": "zh",
        "dialogueSpec": None, "interactiveSpec": None, "pblSpec": None,
        "for_keypoint": "量词只", "teachingObjective": "讲清量词只",
        "estimatedDuration": 90,
    }


def valid_practice() -> dict:
    return {
        "id": "u-4", "type": "practice", "title": "量词只巩固",
        "keyPoints": ["量词'只'用于个体量词"],
        "forbidden_errors": [],
        "context": {"curriculumAt": "kp:只"},
        "self_language": "zh",
        "dialogueSpec": None, "interactiveSpec": None, "pblSpec": None,
        "for_keypoints": ["量词只"],
        "targets_errors": [{"fragment": "*一只鸡", "knowledge_point_id": "kp:只"}],
        "task_kind": "mcq", "questionCount": 3, "difficulty": None,
        "estimatedDuration": 150,
    }


def invalid_explain() -> dict:
    unit = valid_explain()
    unit.pop("teachingObjective")          # 缺必选字段 → code_missing_required
    unit["keyPoints"] = []                 # 空 keyPoints → code_keypoints_empty
    unit["dialogueSpec"] = {"x": 1}        # 后置字段非 null → code_postponed_not_null
    return unit


class MockClient:
    """可脚本化的 mock：chat_json_strict 按序弹出结果（dict | Exception）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_json_strict(self, system, user, temperature=0.2, retries=1):
        self.calls.append(user)
        if not self.responses:
            raise JSONStrictError("mock exhausted")   # 耗尽按解析失败持续降级，不 IndexError
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class GenerationEngineTest(unittest.TestCase):

    def _engine(self, responses, **kw):
        return GenerationEngine(client=MockClient(responses), **kw)

    # ---------------- 验收 1：无 Key 环境降级不崩 ----------------
    def test_no_key_env_degrades_not_crash(self):
        eng = self._engine([JSONStrictError("no key / parse fail")])
        out = eng.generate_unit("explain", {"for_keypoint": "量词只"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "degraded")
        self.assertIn("diagnostics", out)
        codes = [d["code"] for d in out["diagnostics"]]
        self.assertIn("code_invalid_json", codes)
        self.assertEqual(out["attempts"], 2)  # 首轮坏 + 1 轮自愈重试仍坏 → 底部降级

    # ---------------- 验收 2：mock 产出合法单元 → ok:true ----------------
    def test_valid_unit_ok(self):
        eng = self._engine([valid_explain()])
        out = eng.generate_unit("explain", {"for_keypoint": "量词只",
                                            "all_titles": ["上一条"]})
        self.assertTrue(out["ok"])
        self.assertEqual(out["unit"]["type"], "explain")
        self.assertEqual(out["attempts"], 1)
        # authoring 槽位注入 all_titles → LLM 收到的 user prompt 不含"本场已产出标题"空占位
        self.assertIn("上一条", eng.client.calls[0])

    # ---------------- 验收 3：第一轮坏 → 第二轮好 → 自愈成功 ----------------
    def test_self_heal_recovers(self):
        eng = self._engine([JSONStrictError("bad json round1"),
                            valid_practice()])
        out = eng.generate_unit("practice", {"for_keypoints": ["量词只"],
                                             "targets_errors": ["*一只鸡"]},
                                max_repairs=1)
        self.assertTrue(out["ok"])
        self.assertEqual(out["attempts"], 2)
        # 重试轮携带修复反馈
        self.assertIn("修复方向", eng.client.calls[1])

    def test_self_heal_schema_then_recovers(self):
        eng = self._engine([invalid_explain(), valid_explain()])
        out = eng.generate_unit("explain", {"for_keypoint": "量词只"}, max_repairs=1)
        self.assertTrue(out["ok"])
        self.assertEqual(out["attempts"], 2)

    def test_self_heal_exhausted_degrades(self):
        eng = self._engine([invalid_explain(), invalid_explain()])
        out = eng.generate_unit("explain", {"for_keypoint": "量词只"}, max_repairs=1)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "degraded")
        self.assertEqual(out["attempts"], 2)
        codes = {d["code"] for d in out["diagnostics"]}
        self.assertTrue({"code_missing_required", "code_postponed_not_null"} <= codes)
        # best_effort 仍返回（前端口径）
        self.assertIsNotNone(out["unit"])

    # ---------------- 验收 4：写回边界 ----------------
    def test_default_no_writeback(self):
        graph = ErrorGraph("g1")
        eng = self._engine([valid_practice()], graph=graph)
        out = eng.generate_unit("practice", {"for_keypoints": ["量词只"],
                                             "targets_errors": ["*一只鸡"]},
                                write_back=False)
        self.assertTrue(out["ok"])
        self.assertNotIn("writeback", out)          # 默认不自动写回
        self.assertEqual(graph._nodes, {})          # 图谱未被污染

    def test_writeback_graph_when_ok(self):
        graph = ErrorGraph("g2")
        eng = self._engine([valid_practice()], graph=graph)
        out = eng.generate_unit("practice", {"for_keypoints": ["量词只"],
                                             "targets_errors": ["*一只鸡"]},
                                write_back=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["writeback"], ["kp:只"])
        self.assertIn("kp:只", graph._nodes)        # 确认后已入图谱

    def test_writeback_via_writeback_layer(self):
        ledger = MagicMock()
        wb = Writeback(graph=ErrorGraph("g3"), ledger=ledger, root="data")
        eng = self._engine([valid_practice()], graph=wb.graph, writeback=wb)
        out = eng.generate_unit("practice", {"for_keypoints": ["量词只"],
                                             "targets_errors": ["*一只鸡"]},
                                write_back=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["writeback"], ["kp:只"])

    def test_writeback_skips_no_kp(self):
        graph = ErrorGraph("g4")
        eng = self._engine([valid_practice()], graph=graph)
        out = eng.generate_unit("practice", {"for_keypoints": ["量词只"],
                                             "targets_errors": ["*一只鸡"]},
                                write_back=True)
        # targets_errors 含 kp，正常写回：验证"跳过无 kp"分支
        self.assertEqual(out["writeback"], ["kp:只"])

    # ---------------- 其他：非法参数 / degrade 结构完整性 ----------------
    def test_unknown_type_degrades(self):
        # dialogue 已是合法类型（0.22 方向2）；用真正未知的类型测守卫
        eng = self._engine([])
        out = eng.generate_unit("slides", {}, max_repairs=0, write_back=False)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "degraded")
        self.assertEqual(out["attempts"], 0)

    def test_practice_authoring_injects_targets(self):
        eng = self._engine([valid_practice()])
        out = eng.generate_unit("practice", {"for_keypoints": ["量词只"],
                                             "targets_errors": ["*一只鸡"]})
        self.assertTrue(out["ok"])
        self.assertIn("量词只", eng.client.calls[0])
        self.assertIn("*一只鸡", eng.client.calls[0])

    def test_validator_roundtrip(self):
        v = GenerationUnitValidator()
        self.assertEqual(v.validate(valid_explain()), [])

    # ---------------- M9 路 2：explain 联网研究增强 ----------------
    def _search_ok(self, name, params):
        return {"ok": True, "query": params.get("query", ""),
                "results": [{"title": "孔子与其学生", "snippet": "《论语》由孔子及其弟子记录"},
                            {"title": "论语的背景", "snippet": "春秋时期的思想典籍"}]}

    def test_research_applies_to_explain(self):
        eng = self._engine([valid_explain()], search_tool=self._search_ok)
        out = eng.generate_unit("explain", {"for_keypoint": "论语"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["research"]["used"], True)
        self.assertEqual(out["research"]["status"], "ok")
        self.assertEqual(out["research"]["n_sources"], 2)
        # 参考资料被压实进 authoring prompt（只搜一次，进入首轮 user）
        self.assertIn("孔子与其学生", eng.client.calls[0])

    def test_research_not_configured_degrades_gracefully(self):
        def tool(name, params=None):
            return {"ok": False, "status": "not_configured",
                    "message": "web_search 未配置 key"}
        eng = self._engine([valid_explain()], search_tool=tool)
        out = eng.generate_unit("explain", {"for_keypoint": "量词只"})
        self.assertTrue(out["ok"])                       # 工具降级不阻塞生成
        self.assertEqual(out["research"]["used"], False)
        self.assertEqual(out["research"]["status"], "not_configured")

    def test_research_tool_exception_degrades_gracefully(self):
        def tool(name, params=None):
            raise RuntimeError("tool explode")
        eng = self._engine([valid_explain()], search_tool=tool)
        out = eng.generate_unit("explain", {"for_keypoint": "量词只"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["research"]["status"], "error")

    def test_research_skip_without_for_keypoint(self):
        eng = self._engine([valid_explain()], search_tool=self._search_ok)
        out = eng.generate_unit("explain", {})
        self.assertTrue(out["ok"])
        self.assertEqual(out["research"]["used"], False)
        self.assertEqual(out["research"]["status"], "skip")

    def test_research_disabled_via_context(self):
        eng = self._engine([valid_explain()], search_tool=self._search_ok)
        out = eng.generate_unit("explain", {"for_keypoint": "量词只",
                                            "research": False})
        self.assertTrue(out["ok"])
        self.assertEqual(out["research"]["status"], "skip")

    def test_practice_not_researched(self):
        eng = self._engine([valid_practice()], search_tool=self._search_ok)
        out = eng.generate_unit("practice", {"for_keypoints": ["量词只"],
                                             "targets_errors": ["*一只鸡"]})
        self.assertTrue(out["ok"])
        self.assertEqual(out["research"]["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
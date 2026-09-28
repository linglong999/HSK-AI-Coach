# ============================================================
# 契约 v2 · 软评分（soft score）纯确定性测试 —— 不起 LLM
# 覆盖（实施计划 B0 D1/D2/D3）：
#   - derive_verdict 阈值表驱动（0.9→error / 0.7→edge / 0.5→acceptable
#     / None→None / "0.9"(str)→None 非法类型/布尔回归）
#   - 识别层顶层聚合：多条目取 min；无偏误 → acceptable/1.0；降级路径无软评分
#   - 条目 verdict 由 score 确定性派生；mock 无 score → None 向后兼容
#   - ordered_error v1/v2 双版本键集（v1=7 键、v2=10 键）
# 运行: python -m unittest tests.test_soft_score -v
# ============================================================

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.recognizer import Recognizer, derive_verdict
from engine.router import ordered_error


class FakeLLM:
    """脚本化 LLM：返回固定 raw（含/不含 score 均可）。"""

    def __init__(self, raw=None):
        self.raw = raw if raw is not None else {"errors": []}

    def chat_json(self, system, user, **kw):
        return self.raw


class RaisingLLM:
    """识别主链抛异常，触发 _rule_fallback 降级路径。"""

    def chat_json(self, system, user, **kw):
        raise RuntimeError("recognition down")


def _recognizer(raw=None):
    return Recognizer(client=FakeLLM(raw))


# ---------------- ① derive_verdict 阈值表驱动 ----------------

class DeriveVerdictTest(unittest.TestCase):

    def test_threshold_table(self):
        cases = [(0.9, "error"), (0.85, "error"), (0.7, "edge"), (0.6, "edge"),
                 (0.5, "acceptable"), (0.0, "acceptable")]
        for score, want in cases:
            self.assertEqual(derive_verdict(score), want, f"score={score}")

    def test_invalid_inputs_none(self):
        # 缺省/非法类型 → None，不破坏缺省（规则：非数值一律 None）
        self.assertIsNone(derive_verdict(None))
        self.assertIsNone(derive_verdict("0.9"))   # 字符串非法
        self.assertIsNone(derive_verdict(True))    # 布尔（int 子类）非法
        self.assertIsNone(derive_verdict([]))


# ---------------- ② 识别层顶层聚合 ----------------

class TopLevelAggregationTest(unittest.TestCase):

    def test_min_drives_sentence_level(self):
        # 多条目取最差值（min）：0.5 → 句子级 acceptable（宁漏勿错，整体不被高置信条带偏）
        raw = {"errors": [
            {"fragment": "a", "correction": "b", "type": "语法",
             "type_confident": True, "confidence": 0.9, "knowledge_point_id": "",
             "score": 0.95},
            {"fragment": "c", "correction": "d", "type": "词汇",
             "type_confident": True, "confidence": 0.9, "knowledge_point_id": "",
             "score": 0.5},
        ]}
        res = _recognizer(raw).recognize("测试句子。")
        self.assertEqual(res["score"], 0.5)
        self.assertEqual(res["verdict"], "acceptable")

    def test_no_errors_acceptable_10(self):
        res = _recognizer({"errors": []}).recognize("你好。")
        self.assertEqual(res["verdict"], "acceptable")
        self.assertEqual(res["score"], 1.0)

    def test_entry_verdict_derived(self):
        raw = {"errors": [
            {"fragment": "a", "correction": "b", "type": "语法",
             "type_confident": True, "confidence": 0.9, "knowledge_point_id": "",
             "score": 0.7},
        ]}
        res = _recognizer(raw).recognize("测试句子。")
        self.assertEqual(res["errors"][0]["verdict"], "edge")

    def test_entry_missing_score_backward_compat(self):
        # mock/client 不给 score → 条目 score/verdict = None，主链不炸
        raw = {"errors": [
            {"fragment": "a", "correction": "b", "type": "语法",
             "type_confident": True, "confidence": 0.9, "knowledge_point_id": ""},
        ]}
        res = _recognizer(raw).recognize("测试句子。")
        err = res["errors"][0]
        self.assertIsNone(err["score"])
        self.assertIsNone(err["verdict"])

    def test_rule_fallback_no_soft_score(self):
        # 降级路径：宁漏勿错——无软评分（顶层/条目 verdict=score 均缺省 None）
        rec = Recognizer(client=RaisingLLM())
        res = rec.recognize("你好。")
        self.assertEqual(res["errors"], [])
        self.assertIsNone(res.get("verdict"))
        self.assertIsNone(res.get("score"))


# ---------------- ③ ordered_error v1/v2 双版本键集 ----------------

class OrderedErrorVersionTest(unittest.TestCase):

    def test_v1_seven_keys(self):
        # 历史契约：v1 只保留 7 旧键（加新键值也剥掉）
        e = {"fragment": "a", "correction": "b", "type": "语法",
             "type_confident": True, "confidence": 0.9, "knowledge_point_id": "",
             "uncertain": False, "score": 0.9, "verdict": "error",
             "construction_diagnostics": {"matched_rule": "x"}}
        self.assertEqual(
            list(ordered_error(e, version=1).keys()),
            ["fragment", "correction", "type", "type_confident",
             "confidence", "knowledge_point_id", "uncertain"])

    def test_v2_eleven_keys_with_none_defaults(self):
        # 缺软评分的条目 → v2 仍 11 键恒在、值 None/占位（向后兼容；offset 为 A 面可选键）
        e = {"fragment": "a", "correction": "b", "type": "语法",
             "type_confident": True, "confidence": 0.9, "knowledge_point_id": "",
             "uncertain": False}
        out = ordered_error(e)
        self.assertEqual(
            list(out.keys()),
            ["fragment", "correction", "type", "type_confident", "confidence",
             "knowledge_point_id", "uncertain", "score", "verdict",
             "construction_diagnostics", "offset"])
        self.assertIsNone(out["score"])
        self.assertIsNone(out["verdict"])
        self.assertIsNone(out["construction_diagnostics"])

    def test_v2_preserves_values(self):
        e = {"fragment": "a", "correction": "b", "type": "语法",
             "type_confident": True, "confidence": 0.9, "knowledge_point_id": "",
             "uncertain": False, "score": 0.7, "verdict": "edge"}
        out = ordered_error(e)
        self.assertEqual(out["score"], 0.7)
        self.assertEqual(out["verdict"], "edge")
        self.assertIsNone(out["construction_diagnostics"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
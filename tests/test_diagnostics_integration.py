# ============================================================
# B1 构造诊断器 · 主路径接入集成测试（mock LLM client）
# 覆盖：漏报补漏 / 提升联动(uncertain→confirmed) / 复核挂载 / acceptable 不扰动
#       / 降级路径(_rule_fallback 构式补报) / 识别异常走降级路径
# 运行: python -m unittest tests.test_diagnostics_integration -v
# ============================================================

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.recognizer import Recognizer


class _FakeClient:
    """替身 LLM：可返回固定识别结果，或抛异常（触发降级路径）。"""

    def __init__(self, result=None, boom=False):
        self._result = result or {"errors": []}
        self._boom = boom

    def chat_json(self, system, user, **kw):
        if self._boom:
            raise RuntimeError("injected LLM failure")
        return self._result


def _rec(entry):
    """单条 error 最小结构（走 _normalize 补齐缺省）"""
    return {
        "fragment": entry.get("fragment", ""),
        "correction": entry.get("correction", ""),
        "type": entry.get("type", "语法"),
        "type_confident": entry.get("type_confident", True),
        "confidence": entry.get("confidence", 0.9),
        "knowledge_point_id": entry.get("knowledge_point_id", ""),
    }


class DiagnosticsIntegrationTest(unittest.TestCase):

    def _make(self, fake):
        r = Recognizer(client=fake)
        return r

    def test_miss_fill_confirmed(self):
        """漏报补漏：LLM 对「把这件事知道」返回空 → 诊断器补报 confirmed 一条"""
        r = self._make(_FakeClient({"errors": []}))
        res = r.recognize("我把这件事知道", level=3, native_lang="zh")
        self.assertEqual(len(res["errors"]), 1)
        e = res["errors"][0]
        self.assertEqual(e["knowledge_point_id"], "kp-ba-sentence")
        self.assertAlmostEqual(e["confidence"], 0.40)
        self.assertEqual(e["verdict"], "error")
        self.assertEqual(e["construction_diagnostics"]["_main"]["code"], "c5")
        self.assertTrue(e["kp_in_list"])
        self.assertEqual(e["source"], "construction_diagnostics")

    def test_lift_uncertain_to_confirmed(self):
        """提升联动：LLM 报该句但 confidence=0.55 落 uncertain → 诊断器 error 提升 confirmed"""
        fake = _FakeClient({"errors": [_rec({
            "fragment": "这件事知道", "confidence": 0.55,
            "type_confident": False})]})
        r = self._make(fake)
        res = r.recognize("我把这件事知道", level=3, native_lang="zh")
        self.assertEqual(len(res["uncertain"]), 0)
        self.assertEqual(len(res["errors"]), 1)
        e = res["errors"][0]
        self.assertEqual(e["verdict"], "error")          # override（规则>连续）
        self.assertFalse(e["uncertain"])
        self.assertIn("construction_diagnostics", e)    # trace 挂载

    def test_review_attach_no_restrata(self):
        """复核挂载：LLM 已 confirmed → 挂 trace、verdict override error、分层不动"""
        fake = _FakeClient({"errors": [_rec({
            "fragment": "把这件事知道", "confidence": 0.9,
            "type_confident": True})]})
        r = self._make(fake)
        res = r.recognize("我把这件事知道", level=3, native_lang="zh")
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(len(res["uncertain"]), 0)
        e = res["errors"][0]
        self.assertEqual(e["verdict"], "error")
        self.assertIn("construction_diagnostics", e)

    def test_acceptable_does_not_disturb_confirmed(self):
        """acceptable 不扰动：CLN-016 诊断器判 acceptable → 不加 trace、不改分层"""
        fake = _FakeClient({"errors": [_rec({
            "fragment": "考虑一下", "confidence": 0.9, "type_confident": True})]})
        r = self._make(fake)
        res = r.recognize("请你把这个问题认真考虑一下",
                          level=3, native_lang="zh")
        self.assertEqual(len(res["errors"]), 1)
        self.assertNotIn("construction_diagnostics", res["errors"][0])
        self.assertNotEqual(res["errors"][0].get("source"), "construction_diagnostics")

    def test_edge_goes_uncertain(self):
        """edge 补报 → uncertain（触发复核），不进 confirmed"""
        fake = _FakeClient({"errors": []})
        r = self._make(fake)
        res = r.recognize("我看了一遍书", level=3, native_lang="zh")
        # 动量词+宾语 → 补语 C5 edge → uncertain
        self.assertEqual(len(res["errors"]), 0)
        self.assertTrue(any(e.get("source") == "construction_diagnostics"
                            and e["construction_diagnostics"]["_main"]["code"] == "comp_c5"
                            for e in res["uncertain"]))

    def test_fallback_path_adds_construction(self):
        """降级路径：_rule_fallback 对「把饭没吃完」产出构式补报（C4 error）"""
        r = self._make(_FakeClient())
        res = r._rule_fallback("我把饭没吃完", beyond=[], reason="x", level=3)
        self.assertTrue(any(e["source"] == "construction_diagnostics"
                            and e["construction_diagnostics"]["_main"]["code"] == "c4"
                            for e in res["errors"]), res["errors"])

    def test_recognize_exception_goes_fallback_with_diag(self):
        """LLM 异常 → 走降级路径且诊断器照补构式 confirmed"""
        r = self._make(_FakeClient({"errors": []}, boom=True))
        res = r.recognize("我把饭没吃完", level=3, native_lang="zh")
        self.assertTrue(any(e["source"] == "construction_diagnostics"
                            for e in res["errors"]))


if __name__ == "__main__":
    unittest.main()
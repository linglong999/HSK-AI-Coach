# ============================================================
# B1 · golden dry-run（验收：把字句句例无新增误报/漏报）
# 遍历 datasets/eval/golden_v1_4.json 全部含"把"句例：
#   clean（original==corrected）→ 诊断器不产 error（无误报）
#   ba 语序偏误句（在处所前置）→ 诊断器仍报 C6 位次（不漏报）
# 运行: python -m unittest tests.test_golden_dryrun -v
# ============================================================

import json
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.construction_diagnostics import run_construction_diagnostics

GOLDEN = os.path.join(_PROJECT_ROOT, "datasets", "eval", "golden_v1_4.json")


def _ba_items():
    with open(GOLDEN, encoding="utf-8") as f:
        g = json.load(f)
    for key in ("seed_golden", "adversarial"):
        for it in g.get(key, []):
            if "把" in (it.get("original") or ""):
                yield it


def _errors(sent):
    return [d for d in run_construction_diagnostics(sent, 3, [])
            if d["verdict"] == "error"]


class GoldenBaDryRunTest(unittest.TestCase):

    def test_clean_ba_no_false_positive(self):
        """4 条 clean 把字句（把杯子打破了/考虑一下/手机丢了/饭吃了）不误报"""
        cleaned = [it for it in _ba_items()
                   if it["original"] == it.get("corrected", "")]
        self.assertTrue(cleaned, "golden 应至少含 clean 把字句锚点")
        for it in cleaned:
            self.assertEqual(_errors(it["original"]), [], it["original"])

    def test_err_ba_still_flagged_c6(self):
        """ERR-005「把书在桌子上放了」仍报，主查 C6（处所短语前置错位）"""
        flagged = [it for it in _ba_items()
                   if it["original"] != it.get("corrected", "")
                   and "在" in it["original"]]
        self.assertTrue(flagged, "golden 应收敛慢一条 ba 语序偏误锚点")
        for it in flagged:
            errs = _errors(it["original"])
            self.assertTrue(errs, f"偏误把字句漏报: {it['original']}")
            self.assertEqual(errs[0]["_main"]["code"], "c6", it["original"])

    def test_all_ba_anchors_present(self):
        """验收 5 锚点句应全部在 golden 中"""
        txts = [it["original"] for it in _ba_items()]
        for anchor in ("我把书在桌子上放了。", "他把杯子打破了。",
                       "请你把这个问题认真考虑一下。",
                       "他把我的手机丢了。", "我把饭吃了。"):
            self.assertIn(anchor, txts)


if __name__ == "__main__":
    unittest.main()
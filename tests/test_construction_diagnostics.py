# ============================================================
# B1 双轨硬校诊断器 · 纯确定性测试（零 LLM、零 mock）
# 覆盖：§2 ⑧ 全用例表驱动 + R 白名单专项 + golden 五锚点
# 运行: python -m unittest tests.test_construction_diagnostics -v
# ============================================================

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.construction_diagnostics import run_construction_diagnostics


class BaDiagnosticsTest(unittest.TestCase):
    """把字句 6 查"""

    def _ba(self, sent):
        return [d for d in run_construction_diagnostics(sent, 3)
                if d["construction"] == "ba"]

    def test_pass_completed(self):
        d = self._ba("我把作业做完了")
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["verdict"], "acceptable")

    def test_c3_bare_verb_fail(self):
        d = self._ba("我把饭吃")
        self.assertEqual(d[0]["verdict"], "error")
        self.assertEqual(d[0]["score"], 0.40)
        self.assertEqual(d[0]["_main"]["code"], "c3")

    def test_c5_sensory_fail_not_c3(self):
        """验收：主查=C5 非 C3（认知动词非处置义优先于光杆）"""
        d = self._ba("我把这件事知道")
        self.assertEqual(d[0]["verdict"], "error")
        self.assertEqual(d[0]["_main"]["code"], "c5")
        # checks 里 c3 也是 fail（光杆），但主错必须取优先级更高的 c5
        codes = {c["code"]: c["result"] for c in d[0]["checks"]}
        self.assertEqual(codes["c3"], "fail")
        self.assertEqual(codes["c5"], "fail")
        self.assertEqual(d[0]["_main"]["code"], "c5")

    def test_c4_neg_modal_fail(self):
        d = self._ba("我把饭没吃完")
        self.assertEqual(d[0]["verdict"], "error")
        self.assertEqual(d[0]["_main"]["code"], "c4")

    def test_c4_superiority_over_c5(self):
        """优先级链 C4>C5：同时命中时主错取 c4"""
        d = self._ba("我把这件事没知道")
        self.assertEqual(d[0]["_main"]["code"], "c4")

    def test_c2_definiteness_edge(self):
        d = self._ba("我把一本书买了")
        self.assertEqual(d[0]["verdict"], "edge")
        self.assertEqual(d[0]["score"], 0.70)

    def test_c6_placement_fail(self):
        """golden ERR-005 型：处所短语前置 → C6 位次"""
        d = self._ba("我把书在桌子上放了")
        self.assertEqual(d[0]["verdict"], "error")
        self.assertEqual(d[0]["_main"]["code"], "c6")

    def test_golden_clean_anchors(self):
        """ADV-003 / CLN-010 / CLN-019 三句 clean 不误报"""
        for s in ("我把饭吃了", "他把杯子打破了", "他把我的手机丢了"):
            d = self._ba(s)
            # 有把实例，但判定为 acceptable（无 error）
            self.assertFalse(any(x["verdict"] == "error" for x in d), s)

    def test_cln016_consider_once(self):
        """CLN-016「把这个问题认真考虑一下」判 clean（"一下"在 R 白名单）"""
        s = "请你把这个问题认真考虑一下"
        d = self._ba(s)
        self.assertTrue(d)
        self.assertFalse(any(x["verdict"] == "error" for x in d), d)

    def test_colloquial_tail(self):
        """口语致使性尾成分：把妈妈想死了 / 把书包累坏了 → clean"""
        for s in ("把妈妈想死了", "把书包累坏了"):
            d = self._ba(s)
            self.assertFalse(any(x["verdict"] == "error" for x in d), s)

    def test_disposal_form(self):
        """处置义构成：把朋友当作兄弟 → clean"""
        d = self._ba("把朋友当作兄弟")
        self.assertFalse(any(x["verdict"] == "error" for x in d))

    def test_encounter_verb(self):
        """遭遇类：把钥匙忘了 → clean（C3 不误报）"""
        d = self._ba("把钥匙忘了")
        self.assertTrue(d)
        self.assertFalse(any(x["verdict"] == "error" for x in d))

    def test_legal_placement_not_misreported(self):
        """合法"把+O+V+在+处所"（书放在桌子上）不误报 C6"""
        d = self._ba("把书放在桌子上")
        self.assertFalse(any(x["verdict"] == "error" for x in d))

    def test_multi_ba_instances(self):
        """多"把"实例各产一条"""
        d = self._ba("我把饭吃了，他把衣服洗了")
        self.assertEqual(len(d), 2)


class ComplementDiagnosticsTest(unittest.TestCase):
    """补语 5 查"""

    def _comp(self, sent):
        return [x for x in run_construction_diagnostics(sent, 3)
                if x["construction"] == "complement"]

    def test_c1_result_order(self):
        d = self._comp("我做了完作业")
        self.assertTrue(any(x["verdict"] == "error" for x in d), d)
        self.assertTrue(any(x["_main"]["code"] == "comp_c1" for x in d))

    def test_c2_directional_obj(self):
        d = self._comp("我走进去教室")
        self.assertTrue(any(x["verdict"] == "error" for x in d), d)
        self.assertTrue(any(x["_main"]["code"] == "comp_c2" for x in d))

    def test_c3_potential_bare(self):
        d = self._comp("我做得")
        self.assertTrue(any(x["verdict"] == "error" for x in d), d)
        self.assertTrue(any(x["_main"]["code"] == "comp_c3" for x in d))

    def test_c5_measure_edge(self):
        d = self._comp("我看了两遍书")
        self.assertTrue(any(x["verdict"] == "edge" for x in d), d)
        self.assertTrue(any(x["_main"]["code"] == "comp_c5" for x in d))

    def test_no_trigger(self):
        self.assertEqual(run_construction_diagnostics("我周末去爬山", 3), [])


class GoldenDryRunTest(unittest.TestCase):
    """golden 五锚点 dry-run"""

    def test_no_false_positive_on_four_clean(self):
        clean = ["请你把这个问题认真考虑一下",
                 "他把杯子打破了", "他把我的手机丢了", "我把饭吃了"]
        for s in clean:
            d = run_construction_diagnostics(s, 3)
            self.assertFalse(any(x["verdict"] == "error" for x in d),
                             f"{s} -> {d}")

    def test_err005_still_flagged(self):
        d = run_construction_diagnostics("我把书在桌子上放了", 3)
        error_ba = [x for x in d if x["construction"] == "ba"]
        self.assertEqual(len(error_ba), 1)
        self.assertEqual(error_ba[0]["verdict"], "error")
        self.assertEqual(error_ba[0]["_main"]["code"], "c6")


if __name__ == "__main__":
    unittest.main()
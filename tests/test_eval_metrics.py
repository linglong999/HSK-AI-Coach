# -*- coding: utf-8 -*-
# tests/test_eval_metrics.py —— 三集拆分 + bootstrap CI 的回归测试
import unittest

from engine.eval_metrics import subset_of, rate_ci, metrics_for_rows, report


# 构造三集齐全的合成结果（确定性）
def _mk_rows():
    return [
        # 种子偏误（命中 + 漏检）
        {"id": "HSK1-ERR-001", "golden_span": "一个猫", "golden_type": "语法",
         "status": "TP", "det": "一个猫(语法)"},
        {"id": "HSK1-ERR-002", "golden_span": "苹果很多", "golden_type": "语法",
         "status": "TP", "det": "苹果很多(词汇)"},          # 命中但类型错
        {"id": "HSK2-ERR-003", "golden_span": "比我很高", "golden_type": "语法",
         "status": "FN", "det": "—"},
        # 干净对照
        {"id": "HSK1-CLN-004", "golden_span": None, "golden_type": None,
         "status": "TN", "det": "—"},
        {"id": "HSK2-CLN-005", "golden_span": None, "golden_type": None,
         "status": "FP", "det": "买了很多(数量词)"},          # 干净误报
        # 对抗：一偏误一应干净
        {"id": "HSK2-ADV-001", "golden_span": "我他", "golden_type": "语法",
         "status": "TP", "det": "我他(语法)"},
        {"id": "HSK3-ADV-002", "golden_span": None, "golden_type": None,
         "status": "FP", "det": "很多苹果(数量短语)"},         # 过度纠正
    ]


class TestSubset(unittest.TestCase):
    def test_classify(self):
        rows = _mk_rows()
        self.assertEqual([subset_of(r) for r in rows],
                         ["error", "error", "error",
                          "clean", "clean",
                          "adversarial", "adversarial"])


class TestRateCi(unittest.TestCase):
    def test_ci_bounds(self):
        ci = rate_ci(90, 100, seed=1)
        self.assertIsNotNone(ci)
        self.assertEqual(ci["n"], 100)
        self.assertAlmostEqual(ci["value"], 0.9, places=3)
        # CI 应包含点估计附近，且 lo <= hi
        self.assertLessEqual(ci["ci"][0], ci["ci"][1])

    def test_zero_den_returns_none(self):
        self.assertIsNone(rate_ci(0, 0))

    def test_reproducible(self):
        a = rate_ci(30, 40, seed=7)
        b = rate_ci(30, 40, seed=7)
        self.assertEqual(a, b)


class TestReport(unittest.TestCase):
    def test_structure_and_splits(self):
        rep = report(_mk_rows(), n_boot=500)
        subs = rep["subsets"]
        self.assertEqual(subs["error"]["n"], 3)
        # recall = 2/3；type_acc：命中 2 个里 1 个类型对 = 1/2
        self.assertAlmostEqual(subs["error"]["recall"]["value"], 2 / 3, places=3)
        self.assertAlmostEqual(subs["error"]["type_acc"]["value"], 0.5, places=3)
        # 干净误报 1/2
        self.assertEqual(subs["clean"]["n"], 2)
        self.assertAlmostEqual(subs["clean"]["clean_fp_rate"]["value"], 0.5, places=3)
        # 对抗 overcorrection 1/1，偏误命中 1/1
        self.assertAlmostEqual(subs["adversarial"]["overcorrection_rate"]["value"], 1.0, places=3)
        self.assertAlmostEqual(subs["adversarial"]["recall_on_error"]["value"], 1.0, places=3)
        # pooled 保真：tp=4(fp 误报的 clean1 + adv2... compute below)
        #   TP = ERR1, ERR2, ADV-001 = 3 ；FN = ERR3 = 1
        #   FP = CLN-005, ADV-002 应干净被报 = 2
        self.assertEqual(rep["pooled"]["f1"], round(2 * (3 / 5) * (3 / 4) / (3 / 5 + 3 / 4), 4))

    def test_empty_rows_reports_missing(self):
        rep = report([], n_boot=200)
        self.assertEqual(rep["subsets"]["error"]["n"], 0)
        self.assertIsNone(rep["subsets"]["error"].get("recall"))
        self.assertIn("auto_note", rep)


if __name__ == "__main__":
    unittest.main()
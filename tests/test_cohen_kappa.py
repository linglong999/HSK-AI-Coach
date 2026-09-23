# -*- coding: utf-8 -*-
"""B6 J3 · 手写 kappa 对拍单测（拍板点 1：不引 scikit-learn，纯函数对拍锁正确性）。

覆盖：
- cohen_kappa 二值口径：完全一致=1.0、完全反向≈0、已知小样本手算、退化全同类、
  空/长度不齐 → None（指控返回 0/1，绝不明现错误）。
- quadratic_kappa 序值口径：完全一致=1.0、完全反向小值、相邻档 > 隔档、退化为 1.0。
- compute_calibration 双层：judge_qualified 判定（≥0.70）与 dims 聚合路径。
- 绝不出现"简单一致率"作为一致性的唯一口径（缺口由 kappa 结构保证）。
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.dirname(_HERE)
for p in (_PROJ, os.path.join(_PROJ, "datasets", "eval")):
    if p not in sys.path:
        sys.path.insert(0, p)
from calibrate_judge import (  # noqa: E402
    KAPPA_THRESHOLD,
    cohen_kappa,
    compute_calibration,
    quadratic_kappa,
    sample_cases,
)


class TestCohenKappa(unittest.TestCase):
    def test_perfect_agreement_is_one(self):
        a = ["pass", "pass", "fail", "fail"]
        self.assertEqual(cohen_kappa(a, list(a)), 1.0)

    def test_complete_disagreement_binary(self):
        # 4 组 2×2 完全反向：po=0, pe=0.5 → kappa=-0.5/0.5 = -1.0
        a = ["pass", "pass", "fail", "fail"]
        b = ["fail", "fail", "pass", "pass"]
        self.assertAlmostEqual(cohen_kappa(a, b), -1.0, places=8)

    def test_random_agreement_is_zero(self):
        # 均衡分布随机一致：po≈0.5, pe=0.5 → kappa≈0
        a = ["pass"] * 50 + ["fail"] * 50
        b = ["pass", "fail"] * 50  # 一半对、一半错
        k = cohen_kappa(a, b)
        self.assertAlmostEqual(k, 0.0, places=1)

    def test_known_small_sample_hand_computed(self):
        # 手算：a = [P,F,P,F,P], b = [P,P,P,P,F]
        #   一致位 = idx0(P/P)、idx2(P/P) → po = 2/5 = 0.4
        #   P 边缘：a=3/5, b=4/5 → pa*pb=12/25；F 边缘：a=2/5, b=1/5 → 2/25
        #   pe = 14/25 = 0.56；kappa = (0.4-0.56)/0.44 = -0.16/0.44 = -4/11
        a = ["pass", "fail", "pass", "fail", "pass"]
        b = ["pass", "pass", "pass", "pass", "fail"]
        k = cohen_kappa(a, b)
        self.assertAlmostEqual(k, -4 / 11, places=8)

    def test_empty_returns_none(self):
        self.assertIsNone(cohen_kappa([], []))

    def test_length_mismatch_returns_none(self):
        self.assertIsNone(cohen_kappa(["pass"], ["pass", "fail"]))

    def test_degenerate_single_class_is_one(self):
        # 全同一切类：pe=1 → 退化，视为完全一致 = 1.0（不报 NaN）
        self.assertEqual(cohen_kappa(["pass"] * 5, ["pass"] * 5), 1.0)


class TestQuadraticKappa(unittest.TestCase):
    def test_perfect_agreement_is_one(self):
        a = [2, 4, 5, 1, 3]
        self.assertEqual(quadratic_kappa(a, list(a)), 1.0)

    def test_off_by_one_beats_off_by_three(self):
        # 相邻档（差 1）一致性应高于隔三档（差 3）——序值加权惩罚合理
        base = [1, 2, 3, 4, 5, 4, 3, 2, 1, 5]
        near = [(x + 1 if x < 5 else x - 1) for x in base]
        far = [6 - x if x != 1 else 5 for x in base]
        self.assertGreater(quadratic_kappa(base, near),
                           quadratic_kappa(base, far))

    def test_opposite_ends_low(self):
        a = [1, 1, 5, 5]
        b = [5, 5, 1, 1]
        k = quadratic_kappa(a, b)
        self.assertLess(k, 0.0)

    def test_empty_returns_none(self):
        self.assertIsNone(quadratic_kappa([], []))

    def test_degenerate_is_one(self):
        self.assertEqual(quadratic_kappa([3] * 4, [3] * 4), 1.0)


class TestComputeCalibration(unittest.TestCase):
    def test_qualified_flag_when_kappa_above_threshold(self):
        # kappa=1.0 → 严达门槛
        jv = ["pass", "fail", "pass", "fail", "pass", "fail"]
        hv = list(jv)
        rep = compute_calibration(jv, hv)
        self.assertTrue(rep["judge_qualified"])
        self.assertEqual(rep["kappa_binary"], 1.0)
        self.assertEqual(rep["threshold"], KAPPA_THRESHOLD)
        self.assertEqual(rep["n"], len(hv))

    def test_not_qualified_when_kappa_below_threshold(self):
        # 完全反向 → kappa=-1.0，拒启
        jv = ["pass", "pass", "fail", "fail"]
        hv = ["fail", "fail", "pass", "pass"]
        rep = compute_calibration(jv, hv)
        self.assertFalse(rep["judge_qualified"])
        self.assertLess(rep["kappa_binary"], KAPPA_THRESHOLD)

    def test_ordinal_aggregates_dimensions(self):
        # 10 维全同 → ordinal kappa=1.0；jv/hv 二值也全同 → binary=1.0
        jv = ["pass"] * 4
        hv = ["pass"] * 4
        dim = [{1: 5, 2: 4, 3: 4, 4: 4, 5: 4, 6: 4, 7: 5, 8: 4, 9: 4, 10: 4}] * 4
        rep = compute_calibration(jv, hv, judge_dims=dim, human_dims=dim)
        self.assertEqual(rep["kappa_ordinal"], 1.0)

    def test_ordinal_skipped_when_dims_missing(self):
        rep = compute_calibration(["pass", "fail"], ["pass", "fail"])
        self.assertIsNone(rep["kappa_ordinal"])

    def test_ordinal_pair_filtering_none(self):
        # dims 里混 None 值 → 只算非空对，仍得 1.0
        jv = ["pass", "pass"]
        hv = ["pass", "pass"]
        jd = [{1: 5, 2: None}, {1: None, 2: 3}]
        hd = [{1: 5, 2: None}, {1: None, 2: 3}]
        rep = compute_calibration(jv, hv, judge_dims=jd, human_dims=hd)
        self.assertEqual(rep["kappa_ordinal"], 1.0)


class TestSample(unittest.TestCase):
    def test_sample_reduces_and_covers_buckets(self):
        cases = []
        for i in range(20):
            cases.append({"id": f"a{i:02d}", "case_type": "gold"
                          if i < 10 else "naturalness_anchor"})
        picked = sample_cases(cases, n=8, seed=7)
        self.assertLessEqual(len(picked), 8)
        types = {c["case_type"] for c in picked}
        self.assertEqual(types, {"gold", "naturalness_anchor"})

    def test_n_gt_total_returns_all(self):
        cases = [{"id": f"c{i}", "case_type": "gold"} for i in range(3)]
        self.assertEqual(len(sample_cases(cases, n=999, seed=0)), 3)

    def test_empty_cases(self):
        self.assertEqual(sample_cases([], n=10, seed=0), [])


if __name__ == "__main__":
    unittest.main()
# ============================================================
# P0.8 · YACLC 标注一致性脚本 —— 公式锁定 + 可复跑性单测（修订版）
# 覆盖：
#   - Krippendorff's α：手算已知用例（完美一致=1；一同一异=-1/3）
#   - self_check 自验关（与脚本主入口同一实现路径）
#   - 固定 seed → bootstrap 可复跑（两次调用 α/CI 一致）
# 口径：主口径=修正选择（correction 串）；辅口径=改动幅度分桶；
#       ig 仅描述统计；κ 已全部退出交付物。
# 运行: python -m unittest tests.test_iaa_report -v
# ============================================================

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from iaa_report import (          # noqa: E402
    _boot,
    _ci,
    edits_bucket,
    krippendorff_nominal,
    self_check,
)


class KrippendorffNominalTest(unittest.TestCase):
    """手算已知用例锁定 α 公式正确性。 units 形如 [(uid, [标注者码...])]。"""

    def test_perfect_agreement_two_sentences(self):
        # 两句各自完全一致、共用两类码 → α=1
        units = [("a", [0, 0]), ("b", [1, 1])]
        self.assertAlmostEqual(krippendorff_nominal(units), 1.0, places=9)

    def test_one_same_one_diff_two_sentences(self):
        # 一同一异 [0,0][0,1]（共用 0 类）→ 手算真值 -1/3（Scott's π 才是 0）
        units = [("a", [0, 0]), ("b", [0, 1])]
        self.assertAlmostEqual(krippendorff_nominal(units), -1.0 / 3.0, places=9)

    def test_single_code_across_corpus_returns_nan(self):
        # 全库仅出现一种码 → De=1-1=0 → α 未定义，返回 nan（退化分支，不崩溃）
        units = [("a", [1, 1, 1, 1])]
        self.assertTrue(krippendorff_nominal(units) != krippendorff_nominal(units), "期望 NaN")

    def test_single_annotator_each_unit(self):
        # 每句仅 1 名标注者 → 句内无可观测分歧 Do=0 → 闭合式返回 1.0
        units = [("a", [0]), ("b", [1]), ("c", [2])]
        self.assertAlmostEqual(krippendorff_nominal(units), 1.0, places=9)


class SelfCheckTest(unittest.TestCase):
    def test_self_check_passes(self):
        # 自验关与主入口同路径：全同=1、一同一异=-1/3
        ok, (a1, a2) = self_check()
        self.assertTrue(ok)
        self.assertAlmostEqual(a1, 1.0, places=6)
        self.assertAlmostEqual(a2, -1.0 / 3.0, places=6)


class EditsBucketTest(unittest.TestCase):
    def test_bucket_boundaries(self):
        # 桶界: <1 →0, 1-2 →1, 3-4 →2, 5+ →3
        cases = [(0, 0), (1, 1), (2, 1), (3, 2), (4, 2), (5, 3), (7, 3)]
        for n, expected in cases:
            self.assertEqual(edits_bucket(n), expected, f"edits_count={n}")


class ReproducibilityTest(unittest.TestCase):
    def test_boot_same_seed_same_result(self):
        units = [("1", [1, 1, 1, 0]), ("2", [0, 0, 0, 1]), ("3", [1, 1, 0, 0])]
        a = _boot(units, n=300, seed=7)
        b = _boot(units, n=300, seed=7)
        self.assertEqual(a, b)

    def test_ci_valid_bounds(self):
        units = [("1", [1, 1, 0, 0]), ("2", [0, 1]), ("3", [1, 1, 1])]
        lo, mean, hi = _boot(units, n=300, seed=11)
        self.assertLessEqual(lo, mean)
        self.assertLessEqual(mean, hi)


if __name__ == "__main__":
    unittest.main()
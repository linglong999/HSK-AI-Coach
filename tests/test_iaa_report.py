# ============================================================
# P0.8 · YACLC 标注一致性脚本 —— 公式锁定 + 可复跑性单测
# 覆盖：
#   - Krippendorff α：手算已知用例（完美一致=1；单句 3:1 → -1/3）
#   - Fleiss κ：手算已知用例（完美一致=1）
#   - 固定 seed → bootstrap 结果可复跑（两次调用 α/CI 一致）
# 运行: python -m unittest tests.test_iaa_report -v
# ============================================================

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from iaa_report import (            # noqa: E402
    _boot_alpha,
    _fleiss_kappa_for_bucket,
    krippendorff_alpha,
)


class KappaMathTest(unittest.TestCase):
    """手算已知用例锁定公式正确性。"""

    def test_alpha_perfect_agreement_two_classes(self):
        # 两个句子各自完全一致（a 全 1、b 全 0）；两码均有 → α=1
        units = [("1", "a", [1, 1, 1]), ("2", "b", [0, 0, 0])]
        self.assertAlmostEqual(krippendorff_alpha(units), 1.0, places=6)

    def test_alpha_single_sentence_three_to_one(self):
        # 单句 3 个 1、1 个 0 → α = -1/3（coincidence 手算）
        units = [("1", "a", [1, 1, 1, 0])]
        self.assertAlmostEqual(krippendorff_alpha(units), -1.0 / 3.0, places=6)

    def test_kappa_perfect_agreement_two_classes(self):
        # 同 a 全 1 / b 全 0 → 桶 m=3 → κ=1
        units = [("1", "a", [1, 1, 1]), ("2", "b", [0, 0, 0])]
        self.assertAlmostEqual(_fleiss_kappa_for_bucket(units, 3), 1.0, places=6)

    def test_kappa_near_chance(self):
        # 每句均一半一半 → κ≈0（略低于 0 属多类离散常见）
        units = [("1", "a", [1, 1, 0, 0]), ("2", "b", [1, 1, 0, 0])]
        k = _fleiss_kappa_for_bucket(units, 4)
        self.assertAlmostEqual(k, -1.0 / 3.0, places=6)  # 手算：P=1/3, Pe=0.5 → -1/3


class ReproducibilityTest(unittest.TestCase):
    def test_bootstrap_same_seed_same_result(self):
        units = [("1", "a", [1, 1, 1, 0]), ("2", "b", [0, 0, 0, 1]),
                 ("3", "c", [1, 1, 0, 0])]
        a = _boot_alpha(units, n=500, seed=7)
        b = _boot_alpha(units, n=500, seed=7)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
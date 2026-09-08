# -*- coding: utf-8 -*-
"""P0.6 等级口径（GF 三等九级）——唯一真源 engine/levels.py 单测。

覆盖：
  - GF_BANDS 三等九级（初等1-3/中等4-6/高等7-9）
  - legacy_to_gf：现有图谱/知识点库迁移专用（旧粗分 → GF）
  - 兼容五种输入形态：int / 字符串数字 / "HSK3" / "未知" / None
  - 关键回归：图谱 "HSK3"=旧粗分3 → GF5（严禁误用 HSK_TO_GF 标成 GF3）
  - hsk3_to_gf：服务 P0.15（HSK 大纲级号一一对应），独立于 legacy
运行: python -m unittest tests.test_levels -v
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.levels import (GF_BANDS, LEGACY_TO_GF, HSK_TO_GF,
                           band_name, legacy_to_gf, hsk3_to_gf)


class GfBandsTest(unittest.TestCase):
    def test_three_bands_nine_levels(self):
        expected = {1:"初等",2:"初等",3:"初等",4:"中等",5:"中等",6:"中等",
                    7:"高等",8:"高等",9:"高等"}
        self.assertEqual(GF_BANDS, expected)
        self.assertEqual(len(GF_BANDS), 9)

    def test_band_name(self):
        self.assertEqual(band_name(1), "初等")
        self.assertEqual(band_name(5), "中等")
        self.assertEqual(band_name(9), "高等")
        self.assertEqual(band_name(None), "未定")
        self.assertEqual(band_name(0), "未定")      # 越界


class LegacyToGfTest(unittest.TestCase):
    """现有图谱/知识点库迁移：旧 1-4 粗分 → GF。"""

    def test_legacy_mapping(self):
        self.assertEqual(LEGACY_TO_GF, {1:1, 2:3, 3:5, 4:6})

    def test_key_regression_hsk3_is_legacy3(self):
        # 图谱 "HSK3" = 旧粗分 3 的字符串化 → GF5（中等），禁止误用 HSK_TO_GF=GF3
        self.assertEqual(legacy_to_gf("HSK3"), 5)

    def test_int_inputs(self):
        self.assertEqual(legacy_to_gf(1), 1)
        self.assertEqual(legacy_to_gf(2), 3)
        self.assertEqual(legacy_to_gf(3), 5)
        self.assertEqual(legacy_to_gf(4), 6)

    def test_string_number_inputs(self):
        self.assertEqual(legacy_to_gf("1"), 1)
        self.assertEqual(legacy_to_gf("3"), 5)
        self.assertEqual(legacy_to_gf("4"), 6)

    def test_unknown_and_none_and_out_of_range(self):
        self.assertIsNone(legacy_to_gf("未知"))
        self.assertIsNone(legacy_to_gf("HSK未知"))
        self.assertIsNone(legacy_to_gf(None))
        self.assertIsNone(legacy_to_gf(""))
        self.assertIsNone(legacy_to_gf("HSK9"))   # 越界(非1-4)
        self.assertIsNone(legacy_to_gf(5))        # 越界
        self.assertIsNone(legacy_to_gf(0))

    def test_lowercase_hsk(self):
        self.assertEqual(legacy_to_gf("hsk2"), 3)
        self.assertEqual(legacy_to_gf("Hsk4"), 6)


class Hsk3ToGfTest(unittest.TestCase):
    """HSK 3.0 大纲级号 → GF（仅服务 P0.15 krmanik）。"""

    def test_identity_9(self):
        self.assertEqual(HSK_TO_GF, {i:i for i in range(1,10)})
        for lv in range(1, 10):
            self.assertEqual(hsk3_to_gf(lv), lv)

    def test_string_number(self):
        self.assertEqual(hsk3_to_gf("6"), 6)

    def test_unknown(self):
        self.assertIsNone(hsk3_to_gf(None))
        self.assertIsNone(hsk3_to_gf("未知"))
        self.assertIsNone(hsk3_to_gf(0))
        self.assertIsNone(hsk3_to_gf(10))
        # HSK 串不属于 hsk3_to_gf（那是大纲级号），但用 legacy 可解析——各自独立
        self.assertEqual(legacy_to_gf("HSK3"), 5)


if __name__ == "__main__":
    unittest.main()
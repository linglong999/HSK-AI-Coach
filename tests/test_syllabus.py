# -*- coding: utf-8 -*-
"""P0.15 考纲运行时单测。

覆盖：
  - build_point：各级 id 前缀 / level_gf / band；7-9 合编→gf=None/band=高等
  - by_level：单级命中；7-9 归一命中(7/8/9 均返回合编档)
  - by_id：精确命中；未知 id 抛 SyllabusError
  - search：全文 + 字段限定
  - 入库产物字段齐全（desc/examples/scene_tags/question_tags 预留）
运行: python -m unittest tests.test_syllabus -v
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.syllabus import SyllabusDatabase, SyllabusError, build_point, DATA_PATH


class BuildPointTest(unittest.TestCase):
    def test_level_int_prefix_and_gf(self):
        row = {"类别": "词类", "类别名称": "量词", "细目": "名量词", "语法内容": "本、个"}
        p = build_point(row, 3, 12)
        self.assertEqual(p["id"], "hsk30-g3-012")
        self.assertEqual(p["level"], 3)
        self.assertEqual(p["level_gf"], 3)     # P0.6 hsk3_to_gf
        self.assertEqual(p["band"], "初等")    # GF3→初等

    def test_g79_composite(self):
        row = {"类别": "短语", "类别名称": "固定短语", "细目": "", "语法内容": "爱A不A"}
        p = build_point(row, "7-9", 5)
        self.assertEqual(p["id"], "hsk30-g79-005")   # 合编前缀
        self.assertEqual(p["level"], "7-9")
        self.assertIsNone(p["level_gf"])       # 不强行标单一 GF
        self.assertEqual(p["band"], "高等")    # 恒高等
        self.assertEqual(p["status"], "candidates")   # 两段式默认候选

    def test_reserved_fields_present(self):
        row = {"类别": "句子的类型", "类别名称": "特殊句型", "细目": "比较句3", "语法内容": "（1）A不如B"}
        p = build_point(row, 4, 1)
        for f in ("desc", "examples", "scene_tags", "question_tags", "prereq"):
            self.assertIn(f, p)
        self.assertEqual(p["examples"], [])      # 本轮预留空数组


class _DBMixin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SyllabusDatabase.load(DATA_PATH)


class ByLevelTest(_DBMixin):
    def test_level3_count(self):
        self.assertEqual(len(self.db.by_level(3)), 96)

    def test_level6_count(self):
        self.assertEqual(len(self.db.by_level(6)), 50)

    def test_g79_norm_hit(self):
        # 7-9 合编归一：7/8/9 均返回同一合编档
        for lv in (7, 8, 9):
            pts = self.db.by_level(lv)
            self.assertEqual(len(pts), 134)
            self.assertTrue(all(p["level"] == "7-9" for p in pts))

    def test_single_level_int(self):
        pts = self.db.by_level(1)
        self.assertTrue(all(p["level"] == 1 for p in pts))
        self.assertTrue(all(p["level_gf"] == 1 for p in pts))  # P0.6 无标低问题


class ByIdTest(_DBMixin):
    def test_hit(self):
        p = self.db.by_id("hsk30-g3-012")
        self.assertEqual(p["id"], "hsk30-g3-012")

    def test_unknown_raises(self):
        with self.assertRaises(SyllabusError):
            self.db.by_id("hsk30-g999-000")


class SearchTest(_DBMixin):
    def test_fulltext(self):
        import re
        _p = re.compile(r"[\s“”‘’\"'《》()（）…—]")
        self.assertTrue(self.db.search("把字句"))
        for p in self.db.search("把字句"):
            blob = _p.sub("", p["item"] + p["name"] + p["grammar"])
            self.assertIn("把字句", blob)

    def test_field_limited(self):
        res = self.db.search("量词", field="name")
        self.assertTrue(res)
        self.assertTrue(all(p["name"] == "量词" for p in res))

    def test_empty_keyword(self):
        self.assertEqual(self.db.search(""), [])
        self.assertEqual(self.db.search("   "), [])


class TotalsTest(_DBMixin):
    def test_total_593(self):
        self.assertEqual(len(self.db), 593)

    def test_candidates_status(self):
        # 入库未转正前应全部为 candidates
        self.assertEqual(len(self.db.all(status="candidates")), 593)


if __name__ == "__main__":
    unittest.main()
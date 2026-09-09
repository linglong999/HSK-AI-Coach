# -*- coding: utf-8 -*-
# ============================================================
# P0.14 教材对齐（engine/textbook_map.py）回归测试
# 覆盖：
#   - load_map：正常/缺失/损坏 → 空结构兜底；进程内缓存
#   - active_book：无激活→空串；未知→空；切换换教材
#   - lessons_for_syllabus_ids：不命中→[]（兜底）；命中→课次定位；重复考点去重
#     level 5-9 / 未映射 → [] 不报错（通用库兜底语义）
#   - 多教材配置：不同 book_key 返回各自 units；active 切换生效
#   - lessons_for_kp：syllabus_refs value=true 参与、false 不参与、缺失→[]
#   - lessons_for_scene：kp_ids 驱动；scene 非 dict→[]
# 全用注入 map_data，不依赖真实试卷数据文件。
# 运行: python -m unittest tests.test_textbook_map -v
# ============================================================
import os
import sys
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import engine.textbook_map as tm
from engine.textbook_map import (
    load_map, active_book, units_of, lessons_for_syllabus_ids,
    lessons_for_kp, lessons_for_scene,
)

# ---------------- 最小夹具（含两本书、同册同课聚合 unit、mixed 命中） ----------------
A = {
    "active": "book_a",
    "books": {
        "book_a": {"name": "《A》", "units": [
            {"volume": "V1", "lesson": 3, "title": "量词", "kp_ids": ["g1-001", "g1-002"]},
            {"volume": "V1", "lesson": 8, "title": "动词", "kp_ids": ["g1-003"]},
        ]},
        "book_b": {"name": "《B》", "units": [
            {"volume": "BV", "lesson": 11, "title": "比较", "kp_ids": ["g1-003"]},
        ]},
    },
}
EMPTY_BOOK = {"active": "book_x", "books": {"book_a": {"units": []}}}


class LoadMapTest(unittest.TestCase):
    def tearDown(self):
        tm._map_cache = None

    def test_normal_load(self):
        # 用临时文件喂给 load_map：读文件成功 → active 生效
        import json, tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(A, f, ensure_ascii=False)
            path = f.name
        try:
            self.assertEqual(load_map(path)["active"], "book_a")
        finally:
            os.unlink(path)

    def test_missing_file_returns_empty(self):
        d = load_map("/nonexistent/xx.json")
        self.assertEqual(d["books"], {})

    def test_corrupt_file_returns_empty(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            f.write("{not json!")
            path = f.name
        try:
            d = load_map(path)
            self.assertEqual(d["books"], {})
        finally:
            os.unlink(path)  # bracketed only; keep simple

    def test_cache_reused_after_first_import(self):
        # 进程内缓存：同一路径二次调用返回同一对象引用
        import json, tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(A, f, ensure_ascii=False)
            path = f.name
        try:
            a = load_map(path)
            b = load_map(path)
            self.assertIs(a, b)
        finally:
            os.unlink(path)


class ActiveBookTest(unittest.TestCase):
    def test_active_present(self):
        self.assertEqual(active_book(A), "book_a")

    def test_active_missing_defaults_empty(self):
        self.assertEqual(active_book({"books": {"x": {"units": []}}}), "")

    def test_switch_book(self):
        # active 无 units 的书不影响 active_book 逻辑
        d = {"active": "book_b", "books": {"book_a": {"units": []},
                                           "book_b": {"units": []}}}
        self.assertEqual(active_book(d), "book_b")


class UnitsOfTest(unittest.TestCase):
    def test_known_book_returns_units(self):
        self.assertEqual(len(units_of("book_a", A)), 2)

    def test_unknown_book_empty(self):
        self.assertEqual(units_of("nope", A), [])


class LessonsForIdsTest(unittest.TestCase):
    def test_no_match_returns_empty(self):
        # 未映射 → [] 通用兜底，不报错
        self.assertEqual(lessons_for_syllabus_ids(["zzz"], "book_a", A), [])

    def test_level59_no_match(self):
        # level 5-9 / 超教材范围的 id 不在 units → []
        self.assertEqual(lessons_for_syllabus_ids(["hsk5-001"], "book_a", A), [])

    def test_single_hit(self):
        self.assertEqual(lessons_for_syllabus_ids(["g1-003"], "book_a", A),
                         [{"volume": "V1", "lesson": 8, "title": "动词"}])

    def test_aggregated_unit_hit(self):
        # 同册同课聚合 unit：任一 kp 命中即返回该课，且去重
        hits = lessons_for_syllabus_ids(["g1-001", "g1-002"], "book_a", A)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["lesson"], 3)

    def test_dedup_repeated(self):
        # 多个 id 命中同一课只返回一次
        self.assertEqual(len(lessons_for_syllabus_ids(["g1-001", "g1-002", "g1-003"],
                                                       "book_a", A)), 2)

    def test_empty_ids(self):
        self.assertEqual(lessons_for_syllabus_ids([], "book_a", A), [])

    def test_book_key_selects_units(self):
        # 换教材：同一 id 在 book_b 命中其自身课次
        self.assertEqual(lessons_for_syllabus_ids(["g1-003"], "book_b", A),
                         [{"volume": "BV", "lesson": 11, "title": "比较"}])

    def test_active_book_used_when_key_none(self):
        # book_key 缺省 → active_book（book_a）
        self.assertEqual(lessons_for_syllabus_ids(["g1-001"], None, A)[0]["volume"],
                         "V1")


class LessonsForKpTest(unittest.TestCase):
    def test_true_refs_used(self):
        kp = {"syllabus_refs": {"g1-001": True, "g1-999": False}}
        self.assertEqual(lessons_for_kp(kp, "book_a", A)[0]["lesson"], 3)

    def test_all_false_no_hit(self):
        kp = {"syllabus_refs": {"zzz": False}}
        self.assertEqual(lessons_for_kp(kp, "book_a", A), [])

    def test_missing_refs_empty(self):
        self.assertEqual(lessons_for_kp({"name": "x"}, "book_a", A), [])

    def test_non_dict_empty(self):
        self.assertEqual(lessons_for_kp(None, "book_a", A), [])


class LessonsForSceneTest(unittest.TestCase):
    def test_kp_ids_driven(self):
        self.assertEqual(lessons_for_scene({"kp_ids": ["g1-001"]}, "book_a", A)[0]["lesson"],
                         3)

    def test_non_dict_empty(self):
        self.assertEqual(lessons_for_scene(None, "book_a", A), [])
        self.assertEqual(lessons_for_scene("str", "book_a", A), [])


if __name__ == "__main__":
    unittest.main()
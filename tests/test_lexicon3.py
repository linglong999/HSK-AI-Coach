# -*- coding: utf-8 -*-
"""B8 C1/C3 · lexical 迁 2025《HSK 考试大纲》3.0 —— lexicon_hsk3_2025.json 契约测试

覆盖 B8 测试设计（实施计划 §B8 测试设计 ①-⑤）：
  ① C1 转换断言——词量 1-4 ≈2000、认读 1096、书写 400、word_level 键结构与 2021 版同构
  ② 分轨 schema 校验——char 的 recognize 必填、write 可空，逐项/分布内恰
  ③ 换源后 detect_beyond_level golden 用例重跑（等级漂移断言语义，非双轨——历史 2021=4→3.0=3 不再超纲）
     + recognizer/rag 无 diff 回归（load_lexicon 默认读到 3.0；build_hsk_index 含词表不报错）
  ④ lexicon_hsk3_2025.json 存在且 CC BY-SA note / 真源 URL 字段齐全
  ⑤ 与 2021 表并存互不覆盖、可滚回（load_lexicon(path=21) 读回原表）

运行: python -m unittest tests.test_lexicon3 -v
"""
import json
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
_SRC = os.path.join(_PROJECT_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from engine.recognizer import detect_beyond_level, load_lexicon  # noqa: E402

_DATASETS = os.path.join(_PROJECT_ROOT, "datasets")
LEX3 = os.path.join(_DATASETS, "lexicon_hsk3_2025.json")
LEX21 = os.path.join(_DATASETS, "lexicon_hsk1_4.json")

# 官方 1-4 累计锚定（与 convert_lexicon_3.py 对齐）
ANCHOR_RECOGNIZE = 1096
ANCHOR_WRITE = 400
ANCHOR_WORD_LO, ANCHOR_WORD_HI = 1900, 2100


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class C1ConversionAssertTest(unittest.TestCase):
    """①C1 转换断言：词量/认读/书写锚定 + word_level 结构与 2021 同构"""

    def test_lexicon_file_exists(self):
        self.assertTrue(os.path.isfile(LEX3), "lexicon_hsk3_2025.json 缺失")

    def test_word_count_approx_2000(self):
        d = _load(LEX3)
        n = len(d["word_level"])
        self.assertTrue(ANCHOR_WORD_LO <= n <= ANCHOR_WORD_HI,
                        f"词量 {n} 不在官方约2000范围 [{ANCHOR_WORD_LO},{ANCHOR_WORD_HI}]")
        self.assertEqual(d["stats"]["word_count"], n)

    def test_recognize_count_anchor(self):
        d = _load(LEX3)
        n_rec = sum(1 for v in d["char_level"].values() if v["recognize"] is not None)
        self.assertEqual(n_rec, ANCHOR_RECOGNIZE)
        self.assertEqual(d["stats"]["recognize_char_count"], n_rec)

    def test_write_count_anchor(self):
        d = _load(LEX3)
        n_wri = sum(1 for v in d["char_level"].values() if v["write"] is not None)
        self.assertEqual(n_wri, ANCHOR_WRITE)
        self.assertEqual(d["stats"]["write_char_count"], n_wri)

    def test_word_level_structure_isomorphic_to_2021(self):
        """word_level 字典结构 → 键 dict[str, int 1-4]，与 2021 版同构（仅换源）"""
        d3 = _load(LEX3)["word_level"]
        d21 = _load(LEX21)["word_level"]
        for w, lv in d3.items():
            self.assertIsInstance(w, str)
            self.assertIsInstance(lv, int)
            self.assertTrue(1 <= lv <= 4)
        self.assertIsInstance(d3, dict)
        self.assertIsInstance(d21, dict)
        self.assertEqual(type(d3), type(d21))
        self.assertNotEqual(sorted(d3.items()), sorted(d21.items()),
                            "3.0 与 2021 词表应不同源（非双轨）")

    def test_stats_by_level_consistent(self):
        d = _load(LEX3)
        wl, st = d["word_level"], d["stats"]
        by_lv = {str(lv): 0 for lv in (1, 2, 3, 4)}
        for lv in wl.values():
            by_lv[str(lv)] += 1
        self.assertEqual(by_lv, st["word_by_level"])
        self.assertEqual(sum(st["word_by_level"].values()), len(wl))


class CharTrackSchemaTest(unittest.TestCase):
    """②分轨 schema：recognize 必填、write 可空，逐项/分布内恰"""

    def test_char_level_schema(self):
        d = _load(LEX3)
        for c, v in d["char_level"].items():
            self.assertIn("recognize", v, f"字 {c} 缺 recognize")
            self.assertIn("write", v, f"字 {c} 缺 write")
            self.assertIsNotNone(v["recognize"], f"字 {c} recognize 必填")
            self.assertTrue(1 <= v["recognize"] <= 4)
            if v["write"] is not None:
                self.assertTrue(1 <= v["write"] <= 4)

    def test_all_chars_have_recognize(self):
        d = _load(LEX3)
        n_rec = sum(1 for v in d["char_level"].values() if v["recognize"] is not None)
        self.assertEqual(n_rec, len(d["char_level"]), "全部字都应可认读")

    def test_track_by_level_consistent(self):
        d = _load(LEX3)
        st = d["stats"]
        for lv in ("1", "2", "3", "4"):
            got = sum(1 for v in d["char_level"].values()
                      if v["recognize"] == int(lv))
            self.assertEqual(got, st["recognize_by_level"][lv],
                             f"recognize {lv} 级分布不一致")
        for lv in ("2", "3", "4"):
            got = sum(1 for v in d["char_level"].values()
                      if v["write"] == int(lv))
            self.assertEqual(got, st["write_by_level"][lv], f"write {lv} 级分布不一致")


class DetectBeyondGoldenTest(unittest.TestCase):
    """③换源后 detect_beyond_level golden 用例重跑（等级漂移断言语义，非双轨）+ 无 diff 回归"""

    def test_default_load_resolves_to_30(self):
        # recognizer 换源后：默认 path 读到 3.0 词表（无 diff 回归——键结构不变仅换源）
        lex = load_lexicon()  # 默认 LEXICON_PATH = 3.0
        self.assertEqual(len(lex["word_level"]), _load(LEX3)["stats"]["word_count"])
        self.assertIn("历史", lex["word_level"])

    def test_level_semantics_follow_30_not_2021(self):
        """历史 2021=4 → 3.0=3：learner=2（宽容相邻 >3 超纲）下，3.0 不再超纲。
        非双轨：检测等级口径跟 3.0，golden 期望绑定 3.0 词表。"""
        lex3 = load_lexicon(LEX3)
        lex21 = load_lexicon(LEX21)
        # 3.0：仅 研究(4)>3 超纲；历史(3) 不超纲
        v3 = detect_beyond_level("我研究历史", 2, lex3)
        words3 = {v["word"] for v in v3}
        self.assertEqual(words3, {"研究"})
        self.assertNotIn("历史", words3, "历史 3.0=3 不应再判超纲")
        # 2021 对照：历史(4)>3 超纲 → 证明判定口径已随 3.0 漂移
        v21 = detect_beyond_level("我研究历史", 2, lex21)
        words21 = {v["word"] for v in v21}
        self.assertIn("历史", words21, "2021 下历史=4 应超纲（对照锚）")

    def test_clean_sentence_no_overscope(self):
        lex3 = load_lexicon(LEX3)
        # 苹果 3.0=1，我=1：learner=2 下无超纲
        self.assertEqual(detect_beyond_level("我吃苹果", 2, lex3), [])

    def test_rag_build_index_with_lexicon_no_diff(self):
        # rag build_hsk_index(include_lexicon=True) 换源后无 diff、不报错
        from engine.rag import build_hsk_index
        idx = build_hsk_index(include_lexicon=True, graph=None)
        self.assertTrue(idx is not None)


class LicenseDisclosureTest(unittest.TestCase):
    """④CC BY-SA 披露 + 真源 URL 字段齐全"""

    def test_by_sa_fields_present(self):
        d = _load(LEX3)
        for k in ("source", "crosscheck_source", "version", "note", "license"):
            self.assertTrue(d.get(k), f"缺 {k}")
        self.assertIn("CC BY-SA 4.0", d["license"])
        self.assertIn("github.com", d["source"])
        self.assertIn("github.com", d["crosscheck_source"])

    def test_crosscheck_report_present(self):
        d = _load(LEX3)
        self.assertIn("word_hit", d["crosscheck"])
        self.assertIn("char_hit", d["crosscheck"])
        cm = d.get("crosscheck_mismatch", {})
        self.assertIn("word", cm)
        self.assertIn("char", cm)


class CoexistRollbackTest(unittest.TestCase):
    """⑤与 2021 表并存互不覆盖、可滚回"""

    def test_2021_preserved(self):
        d21 = _load(LEX21)
        self.assertEqual(d21["stats"]["word_count"], 3208, "2021 表应原样保留未被覆盖")
        self.assertIn("word_level", d21)

    def test_rollback_load_21(self):
        # 滚回语义：load_lexicon(path=2021) 读回原表，等级源可切回
        lex21 = load_lexicon(LEX21)
        self.assertEqual(len(lex21["word_level"]), 3208)
        self.assertEqual(lex21["word_level"]["历史"], 4)


if __name__ == "__main__":
    unittest.main()
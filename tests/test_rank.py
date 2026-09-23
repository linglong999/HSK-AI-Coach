# -*- coding: utf-8 -*-
"""B5 I5 · 排序层 engine/sort/rank.py（课程静态序骨架，纯确定性）

覆盖：
  - 主序断言（level 全序列非递减——硬主序）
  - comm≡0.5 时同级=lexicon 原序（稳定排序，不随机不跳动）
  - 白名单词义项粒度（item="{词}-{sense_id}"）/ 其余词形
  - 白名单缺席→退化全词形（软依赖，不受阻塞）
  - 禁越级（极端 scene 构造验证 comm_score 再极端也不越级抬/压级）
  - comm_score 接口位存在且 ≡0.5（未回填兜底）
运行: python -m unittest tests.test_rank -v
"""
import json
import os
import sys
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.sort.rank import (  # noqa: E402
    DEFAULT_COMM_SCORE,
    comm_score,
    levels_of,
    rank_syllabus,
)


def _lex(word_level):
    return {"word_level": word_level, "char_level": {}}


def _write_whitelist(words):
    """写临时白名单文件，返回路径。words: {词: {level, sense_ids}}"""
    payload = {"words": {k: v for k, v in words.items()}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        return f.name


class RankMainOrderTest(unittest.TestCase):
    """主序 = level 升序（硬主序）"""

    def test_level_ascending_main_order(self):
        lex = _lex({"我": 1, "学习": 1, "中国": 2, "历史": 2, "经济": 3})
        ranked = rank_syllabus(lex)
        seq = levels_of(ranked)
        self.assertEqual(seq, sorted(seq))   # 非递减
        self.assertEqual(seq, [1, 1, 2, 2, 3])
        # 等级升序内词保存
        self.assertEqual([r["level"] for r in ranked],
                         [1, 1, 2, 2, 3])

    def test_rank_numbering_starts_1_consecutive(self):
        lex = _lex({"我": 1, "中国": 2})
        ranked = rank_syllabus(lex)
        self.assertEqual([r["rank"] for r in ranked], [1, 2])

    def test_words_already_shuffled_input(self):
        # 输入顺序乱（不应影响主序——按 level 重排）
        lex = _lex({"经济": 3, "我": 1, "历史": 2})
        ranked = rank_syllabus(lex)
        seq = levels_of(ranked)
        self.assertEqual(seq, [1, 2, 3])


class RankCommStableTest(unittest.TestCase):
    """comm≡0.5 → 同级退化为 lexicon 原序（稳定、不跳动）"""

    def test_same_level_lexicon_order(self):
        # 同等级词按 lexicon 给定顺序出（comm 全 0.5 → 稳定排序保持相对序）
        lex = _lex({"苹果": 1, "香蕉": 1, "橘子": 1})
        ranked = rank_syllabus(lex)
        items = [r["item"] for r in ranked]
        self.assertEqual(items, ["苹果", "香蕉", "橘子"])

    def test_comm_score_default(self):
        self.assertEqual(comm_score("任意词"), DEFAULT_COMM_SCORE)
        self.assertEqual(comm_score("任意词", {"场景": 0.9}), DEFAULT_COMM_SCORE)


class RankSenseGranularityTest(unittest.TestCase):
    """排序单元：白名单词义项粒度 / 其余词形"""

    def test_whitelist_word_sense_expansion(self):
        wl = _write_whitelist({"一": {"level": 1, "sense_ids": ["一-01", "一-02"]}})
        lex = _lex({"一": 1, "苹果": 1})
        ranked = rank_syllabus(lex, whitelist_path=wl)
        os.unlink(wl)
        items = [r["item"] for r in ranked]
        # 白名单"一"展开两义项；其余词"苹果"词形
        self.assertIn("一-01", items)
        self.assertIn("一-02", items)
        self.assertIn("苹果", items)
        self.assertNotIn("一", items)   # 白名单词不再以纯词形出

    def test_non_whitelist_word_stays_word_form(self):
        wl = _write_whitelist({"一": {"level": 1, "sense_ids": ["一-01"]}})
        lex = _lex({"苹果": 1})   # 不在白名单 → 词形
        ranked = rank_syllabus(lex, whitelist_path=wl)
        os.unlink(wl)
        self.assertEqual([r["item"] for r in ranked], ["苹果"])

    def test_whitelist_absent_degrades_full_word_form(self):
        # 白名单缺席（路径不存在）→ 退化全词形，且不抛
        lex = _lex({"一": 1, "苹果": 1})
        with tempfile.TemporaryDirectory() as d:
            missing = os.path.join(d, "nope.json")
            ranked = rank_syllabus(lex, whitelist_path=missing)
        items = [r["item"] for r in ranked]
        self.assertIn("一", items)
        self.assertIn("苹果", items)
        self.assertNotIn("一-01", items)

    def test_default_whitelist_path_used_when_none(self):
        # 不给 whitelist_path → 用默认 B4 产物路径（存在则义项粒度生效）
        lex = _lex({})
        # 空词表 → 空输出，不抛（默认路径读取在本机可视情况成败都安全）
        self.assertEqual(rank_syllabus(lex), [])


class RankNoJumpTest(unittest.TestCase):
    """禁越级：comm 极端值不抬/压级"""

    def test_no_jump_with_extreme_comm(self):
        # comm 全极端（临时不借默认）——即便 scene_weights 注入极大差异，
        # 主序仍禁越级：低等级词永不排到高等级词后。
        lex = _lex({"我": 1, "学习": 1, "经济": 3, "哲学": 3})
        # 直接调 comm_score 单测已约束 ≡0.5；此处验证结构上 level 主序优先
        ranked = rank_syllabus(lex, scene_weights={"口语": 0.99, "书面": 0.01})
        seq = levels_of(ranked)
        self.assertEqual(seq, sorted(seq))   # 禁越级成立
        # 所有 level=1 词都在 level=3 词之前
        idx1 = seq.index(1)
        idx3 = seq.index(3)
        self.assertLess(idx1, idx3)


class RankApiShapeTest(unittest.TestCase):
    """输出形状契约"""

    def test_output_fields(self):
        lex = _lex({"我": 1})
        ranked = rank_syllabus(lex)
        self.assertEqual(sorted(ranked[0].keys()),
                         ["comm_score", "item", "level", "rank"])

    def test_levels_of_helper(self):
        ranked = [{"level": 2}, {"level": 1}]
        self.assertEqual(levels_of(ranked), [2, 1])


if __name__ == "__main__":
    unittest.main()
# -*- coding: utf-8 -*-
"""B5 I2 · error_graph.mastered_words()（已掌握词集——脚手架事前约束唯一数据源）

覆盖：
  - 阈值过滤（mastery≥threshold；低于阈值排除）
  - 等级过滤（level 数字 ≤ max_level；等外排除）
  - limit 截断
  - priority 降序（同 mastery 下 error_count 高者优先）
  - 空图谱返回空
  - char: 前缀节点排除（mastery 再高也不进词集）
  - 输出词形粒度（knowledge_point 值，非 kp_id）；senses[] 子卡不单列
运行: python -m unittest tests.test_mastered_words -v
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.graph.error_graph import ErrorGraph  # noqa: E402
from engine.graph.model import Node  # noqa: E402


def _mk(id_, kp, mastery=0.9, level="HSK1", error_count=0):
    """构造图谱词节点（priority 随 error_count 在同 mastery 下升高）。"""
    return Node(id=id_, knowledge_point=kp, level=level,
                mastery=mastery, error_count=error_count, created_at="2020-01-01T00:00:00Z")


class MasteredWordsTest(unittest.TestCase):

    def test_empty_graph_returns_empty(self):
        g = ErrorGraph("m0")
        self.assertEqual(g.mastered_words(), [])

    def test_threshold_filter(self):
        g = ErrorGraph("m1")
        g._nodes = {
            "K1": _mk("K1", "好", mastery=0.9),
            "K2": _mk("K2", "对", mastery=0.5),   # 低于 0.7 → 排除
            "K3": _mk("K3", "是", mastery=0.7),   # ==阈值 → 纳入
        }
        out = g.mastered_words(threshold=0.7)
        self.assertIn("好", out)
        self.assertNotIn("对", out)
        self.assertIn("是", out)

    def test_level_filter(self):
        g = ErrorGraph("m2")
        g._nodes = {
            "K1": _mk("K1", "吃", level="HSK1"),
            "K3": _mk("K3", "买", level="HSK3"),
            "K5": _mk("K5", "游", level="HSK5"),
        }
        out3 = g.mastered_words(max_level=3)
        self.assertIn("吃", out3)
        self.assertIn("买", out3)
        self.assertNotIn("游", out3)
        # 不限等级 → 全含
        self.assertEqual(len(g.mastered_words()), 3)

    def test_limit_cap(self):
        g = ErrorGraph("m3")
        g._nodes = {f"K{i}": _mk(f"K{i}", f"词{i}") for i in range(10)}
        out = g.mastered_words(limit=3)
        self.assertEqual(len(out), 3)

    def test_priority_desc_order(self):
        # 同 mastery 下 error_count 高者 priority 高 → 更靠前
        g = ErrorGraph("m4")
        g._nodes = {
            "K1": _mk("K1", "少错", mastery=0.9, error_count=1),
            "K2": _mk("K2", "多错", mastery=0.9, error_count=4),
        }
        out = g.mastered_words()
        # 多错（priority 高）应排前
        self.assertEqual(out[0], "多错")
        self.assertEqual(out[1], "少错")

    def test_char_nodes_excluded(self):
        # char: 前缀节点 mastery 再高也不进词集
        g = ErrorGraph("m5")
        g._nodes = {
            "K1": _mk("K1", "玻璃", mastery=0.9),
            "char:璃": _mk("char:璃", "璃", mastery=0.99, level="char-HSK0"),
            "char:玻": _mk("char:玻", "玻", mastery=0.99, level="char-HSK0"),
        }
        out = g.mastered_words()
        self.assertEqual(out, ["玻璃"])
        self.assertNotIn("璃", out)
        self.assertNotIn("玻", out)

    def test_word_granularity_kp_value_not_id(self):
        # 输出 knowledge_point 词形，非 kp_id
        g = ErrorGraph("m6")
        g._nodes = {"kp-liangci": _mk("kp-liangci", "量词", mastery=0.9, level="HSK2")}
        out = g.mastered_words()
        self.assertEqual(out, ["量词"])
        self.assertNotIn("kp-liangci", out)

    def test_no_knowledge_point_skipped(self):
        g = ErrorGraph("m7")
        g._nodes = {"K1": _mk("K1", "", mastery=0.9)}   # 无词形 → 跳过
        self.assertEqual(g.mastered_words(), [])

    def test_unknown_level_not_filtered(self):
        # "未知"等级节点：不因等级被排除（但低 mastery 该排仍排）
        g = ErrorGraph("m8")
        g._nodes = {"K1": _mk("K1", "词", mastery=0.9, level="未知")}
        out = g.mastered_words(max_level=2)   # 未知等级 → 不判等级，保留
        self.assertIn("词", out)

    def test_default_threshold_and_limit(self):
        g = ErrorGraph("m9")
        g._nodes = {f"K{i}": _mk(f"K{i}", f"词{i}") for i in range(250)}
        # 默认 limit=200，远超 → 截断
        self.assertEqual(len(g.mastered_words()), 200)


if __name__ == "__main__":
    unittest.main()
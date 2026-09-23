# -*- coding: utf-8 -*-
"""B4 H5 · 字子层（图谱独立 char 节点）—— 跟词带出、幂等、独立 FSRS

覆盖：
  - ensure_char_node 逐字建点：id=char:字、knowledge_point=字、level=char-HSK{n}
  - 幂等：重复 ensure 不重复建、返回仅含真实新建
  - 字级来源 lexicon char_level（未知字回退 char-HSK0）
  - get_char_node 查询
  - 独立 FSRS：改词卡 S 不影响对应字卡（防词频绑架）
  - 不预建全量字表（MVP 范围控制）
运行: python -m unittest tests.test_char_node -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.graph.error_graph import ErrorGraph, CHAR_NODE_PREFIX
from engine.graph.model import Node


def _ts(days_ago: float) -> str:
    t = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class CharNodeEnsureTest(unittest.TestCase):

    def test_ensure_creates_per_char(self):
        g = ErrorGraph("c1")
        created = g.ensure_char_node("学霸")
        self.assertEqual(set(created), {"char:学", "char:霸"})
        for ch in "学霸":
            nd = g.get_char_node(ch)
            self.assertIsNotNone(nd)
            self.assertTrue(nd["id"].startswith(CHAR_NODE_PREFIX))
            self.assertEqual(nd["knowledge_point"], ch)
            self.assertTrue(nd["level"].startswith("char-HSK"))

    def test_char_level_from_lexicon(self):
        g = ErrorGraph("c2")
        g.ensure_char_node("爱")          # 词典 char_level: 爱=1
        self.assertEqual(g.get_char_node("爱")["level"], "char-HSK1")

    def test_unknown_char_falls_back_level0(self):
        g = ErrorGraph("c3")
        g.ensure_char_node("镟")          # 超纲/未收录字
        self.assertEqual(g.get_char_node("镟")["level"], "char-HSK0")

    def test_idempotent_no_rebuild(self):
        g = ErrorGraph("c4")
        first = g.ensure_char_node("学霸")
        second = g.ensure_char_node("学霸")
        self.assertEqual(len(first), 2)
        self.assertEqual(second, [])         # 已存在 → 不重建、返回空
        self.assertIn("char:学", g._nodes)

    def test_blank_ignored(self):
        g = ErrorGraph("c5")
        created = g.ensure_char_node("学 霸")
        self.assertEqual(set(created), {"char:学", "char:霸"})  # 空白不建点


class CharNodeIndependenceTest(unittest.TestCase):

    def test_word_fsrs_does_not_alter_char(self):
        # 词卡独立调度：改词卡 S 不影响字卡（防词频绑架）
        g = ErrorGraph("c6")
        g.ensure_char_node("璃")
        g._nodes["词卡"] = Node(id="词卡", knowledge_point="玻璃", level="HSK1")
        g.review_feedback("词卡", rating=3, event_key="e1")
        self.assertGreater(g.get_kp("词卡")["node"]["fsrs_stability"], 0)
        # 字卡未被词卡复习触碰 → FSRS 仍初值
        self.assertEqual(g.get_char_node("璃")["fsrs_stability"], 0)

    def test_char_node_has_own_fsrs(self):
        g = ErrorGraph("c7")
        g.ensure_char_node("璃")
        cid = f"{CHAR_NODE_PREFIX}璃"
        g._nodes[cid].next_review_at = _ts(days_ago=1)
        g.review_feedback(cid, rating=3, event_key="e2")
        self.assertGreater(g.get_char_node("璃")["fsrs_stability"], 0)
        self.assertIsNotNone(g.get_char_node("璃")["next_review_at"])


class CharNodeScopeTest(unittest.TestCase):

    def test_no_proactive_prebuild(self):
        # MVP 只跟事件增量建点，不预建全量字表
        g = ErrorGraph("c8")
        g.ensure_char_node("学")            # 只轮到一个词
        for key in g._nodes:
            self.assertTrue(key.startswith("char:") or key == "学")
        self.assertEqual(len([k for k in g._nodes if k.startswith("char:")]), 1)

    def test_get_char_node_missing_returns_none(self):
        g = ErrorGraph("c9")
        self.assertIsNone(g.get_char_node("未建"))


if __name__ == "__main__":
    unittest.main()
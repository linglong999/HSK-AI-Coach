# -*- coding: utf-8 -*-
"""P0.4 · priority 分类型加权 + 化石化 测试。
覆盖（用户 P0.4 拍板：A1/A2/A3 + B 追加）：
  - A1：nature=""→1.0（.get 缺省）；错序/误代=1.3；误加/遗漏/未知=1.0
  - A2：化石化口径 连续≥3次未纠正 && 距上次学习≥180天
  - A3：互斥不叠乘——化石=base×1.5，替代 nature 权重
  - 冷数据（last_learnt_at=None）→ days=0 → 永不误标
  - verdict 驱动 streak：pass→0 / fail→+1
  - review_feedback 独立接口：只动 streak，不改 mastery/last_learnt_at/error_count
  - unfixed_streak 落盘、fossilized 现算
运行: python -m unittest tests.test_fossil_priority -v"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.graph.error_graph import (Node, ErrorGraph, NATURE_WEIGHT,
                                      FOSSIL_RULE, FOSSIL_BOOST)
from engine.graph.error_kind_map import resolve


def _ts(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _mk(kp_id, error_count=0, mastery=0.0, nature="", unfixed_streak=0,
        last=None, created=None):
    return Node(id=kp_id, knowledge_point=kp_id, level="HSK1",
                error_count=error_count, mastery=mastery, nature=nature,
                last_learnt_at=last, created_at=created or "2026-08-01T00:00:00Z",
                unfixed_streak=unfixed_streak)


class NatureWeightTest(unittest.TestCase):
    """A1：nature 分类型权重——""→1.0 / 错序·误代=1.3 / 其余=1.0"""

    def _prio(self, node, g=None):
        g = g or ErrorGraph("w")
        return (min(node.error_count, 5) * (1 - node.mastery)
                * g._aging(node) * NATURE_WEIGHT.get(node.nature, 1.0))

    def test_empty_nature_weight_1(self):
        self.assertEqual(NATURE_WEIGHT.get("", 1.0), 1.0)

    def test_unknown_nature_weight_1(self):
        self.assertEqual(NATURE_WEIGHT.get("未知", 1.0), 1.0)

    def test_high_weight_natures(self):
        self.assertEqual(NATURE_WEIGHT.get("错序", 1.0), 1.3)
        self.assertEqual(NATURE_WEIGHT.get("误代", 1.0), 1.3)

    def test_low_weight_natures(self):
        self.assertEqual(NATURE_WEIGHT.get("误加", 1.0), 1.0)
        self.assertEqual(NATURE_WEIGHT.get("遗漏", 1.0), 1.0)


class PriorityWeightOrderTest(unittest.TestCase):
    """A1：same base → 错序/误代 排 误加/遗漏 前"""

    def test_weighted_sort(self):
        g = ErrorGraph("ord")
        older = "2026-08-01T00:00:00Z"
        g._nodes = {
            "A": _mk("A", error_count=5, mastery=0.0, nature="误代", created=older),
            "B": _mk("B", error_count=5, mastery=0.0, nature="遗漏", created=older),
        }
        q = g.get_review_queue()
        self.assertEqual(q[0]["kp_id"], "A")  # 误代1.3 > 遗漏1.0，同 base
        self.assertEqual(q[1]["kp_id"], "B")


class FossilizedTest(unittest.TestCase):
    """A2/A3：判定口径 + 互斥不叠乘 + 冷数据不误标"""

    def _prio(self, g, node):
        return g._priority(node)

    def test_cold_data_never_fossilized(self):
        # last_learnt_at=None → days=0 → 永不误标（冷数据）
        g = ErrorGraph("cold")
        node = _mk("K", error_count=3, unfixed_streak=99, last=None)
        g._refresh_fossilized(node)
        self.assertFalse(node.fossilized)
        # streak 极高但从未学习 → days=0 不触发化石；fossilized=False → 走 nature 权重
        node2 = _mk("K2", error_count=3, unfixed_streak=99, nature="误代", last=None)
        g._refresh_fossilized(node2)
        self.assertFalse(node2.fossilized)
        self.assertEqual(g._priority(node2),
                         min(3, 5) * (1 - 0) * g._aging(node2) * 1.3)

    def test_fossil_requires_both_conditions(self):
        g = ErrorGraph("f")
        # ① streak 够但 days 不够 → 不化石
        node_a = _mk("A", error_count=3, unfixed_streak=3, last=_ts(days_ago=10))
        g._nodes = {"A": node_a}
        g._refresh_fossilized(node_a)
        self.assertFalse(node_a.fossilized)
        # ② days 够但 streak 不够 → 不化石
        node_b = _mk("B", error_count=3, unfixed_streak=2, last=_ts(days_ago=200))
        g._nodes = {"B": node_b}
        g._refresh_fossilized(node_b)
        self.assertFalse(node_b.fossilized)
        # ③ 两者都够 → 化石
        node_c = _mk("C", error_count=3, unfixed_streak=3, last=_ts(days_ago=200))
        g._nodes = {"C": node_c}
        g._refresh_fossilized(node_c)
        self.assertTrue(node_c.fossilized)

    def test_mutual_exclusive_fossil_replaces_nature(self):
        # A3 互斥：化石=base×1.5，替代 nature 权重（不叠乘 1.5×1.3）
        g = ErrorGraph("m")
        node = _mk("C", error_count=3, mastery=0.0, nature="误代",
                   unfixed_streak=3, last=_ts(days_ago=200))
        g._nodes = {"C": node}
        base = min(3, 5) * (1 - 0) * g._aging(node)
        self.assertEqual(g._priority(node), base * FOSSIL_BOOST)  # 非 base*1.5*1.3
        self.assertNotEqual(g._priority(node), base * FOSSIL_BOOST * 1.3)

    def test_fossil_boost_rules(self):
        # 断言 FOSSIL_RULE 值被锁定，防 drift（用户 A2 拍板 3/180）
        self.assertEqual(FOSSIL_RULE, {"unfixed_streak": 3, "days_idle": 180})
        self.assertEqual(FOSSIL_BOOST, 1.5)


class VerdictStreakTest(unittest.TestCase):
    """verdict 驱动 streak：pass→0 / fail→+1"""

    def test_fail_increments_iterations_out(self):
        g = ErrorGraph("v2")
        bias = {"fragment": "f", "type": "语法", "knowledge_point_id": "K",
                "knowledge_point_name": "K", "level": "HSK1"}
        for i in range(3):
            g.ingest_verdict(dict(bias), verdict="fail", uncertain=False,
                             event_key=f"fk{i}")
        nd = g.get_kp("K")["node"]
        self.assertEqual(nd["unfixed_streak"], 3)

    def test_pass_resets_streak(self):
        g = ErrorGraph("v3")
        bias = {"fragment": "f", "type": "语法", "knowledge_point_id": "K",
                "knowledge_point_name": "K", "level": "HSK1"}
        for i in range(2):
            g.ingest_verdict(dict(bias), verdict="fail", uncertain=False,
                             event_key=f"k{i}")
        g.ingest_verdict(dict(bias), verdict="pass", uncertain=False, event_key="k3")
        nd = g.get_kp("K")["node"]
        self.assertEqual(nd["unfixed_streak"], 0)
        self.assertIsNotNone(nd["last_learnt_at"])  # pass touch


class ReviewFeedbackTest(unittest.TestCase):
    """review_feedback 独立接口：只动 streak，不改 mastery/last_learnt_at"""

    def test_feedback_does_not_touch_other_fields(self):
        g = ErrorGraph("rf")
        bias = {"fragment": "f", "type": "语法", "knowledge_point_id": "K",
                "knowledge_point_name": "K", "level": "HSK1"}
        g.ingest_verdict(dict(bias), verdict="pass", uncertain=False, event_key="e0")
        before = g.get_kp("K")["node"]
        g.review_feedback("K", correct=False, event_key="r1")
        nd = g.get_kp("K")["node"]
        self.assertEqual(nd["unfixed_streak"], 1)
        self.assertEqual(nd["mastery"], before["mastery"])          # 不改 mastery
        self.assertEqual(nd["last_learnt_at"], before["last_learnt_at"])  # 不 touch
        self.assertEqual(nd["error_count"], before["error_count"])  # 不改计数

    def test_feedback_correct_resets(self):
        g = ErrorGraph("rf2")
        bias = {"fragment": "f", "type": "语法", "knowledge_point_id": "K",
                "knowledge_point_name": "K", "level": "HSK1"}
        g.ingest_verdict(dict(bias), verdict="fail", uncertain=False, event_key="e0")
        g.review_feedback("K", correct=False, event_key="r1")
        g.review_feedback("K", correct=False, event_key="r2")
        g.review_feedback("K", correct=True, event_key="r3")
        nd = g.get_kp("K")["node"]
        self.assertEqual(nd["unfixed_streak"], 0)

    def test_feedback_idempotent(self):
        g = ErrorGraph("rf3")
        bias = {"fragment": "f", "type": "语法", "knowledge_point_id": "K",
                "knowledge_point_name": "K", "level": "HSK1"}
        g.ingest_verdict(dict(bias), verdict="fail", uncertain=False, event_key="e0")
        g.review_feedback("K", correct=False, event_key="r")
        g.review_feedback("K", correct=False, event_key="r")  # 重放应跳
        nd = g.get_kp("K")["node"]
        # verdict fail=1 + feedback 一次=2；重放被幂等跳过 → 仍 2（幂等失效会变 3）
        self.assertEqual(nd["unfixed_streak"], 2)


class PersistenceTest(unittest.TestCase):
    """unfixed_streak 落盘；fossilized 现算不落盘"""

    def test_streak_persists_fossil_recomputed(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "g.json")
            from engine.graph.error_graph import (ErrorGraph, Node,
                                                  FOSSIL_RULE)
            g = ErrorGraph("rt")
            node = Node(id="C", knowledge_point="C", level="HSK1",
                        error_count=3, unfixed_streak=3)
            node.last_learnt_at = _ts(days_ago=200)
            g._nodes = {"C": node}
            g.save(p)
            g2 = ErrorGraph("rt")
            g2.load(p)
            nd = g2.get_kp("C")["node"]
            self.assertEqual(nd["unfixed_streak"], 3)   # streak 落盘
            self.assertNotIn("fossilized", nd)          # fossilized 现算不进 dict
            # 读 priority 触发 refresh → 现算为化石
            node2 = g2._nodes["C"]
            g2._refresh_fossilized(node2)
            self.assertTrue(node2.fossilized)


class ColdDataBackCompatTest(unittest.TestCase):
    """旧数据无 unfixed_streak → load 兜底 0，不炸"""

    def test_load_old_no_streak_defaults_zero(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "g.json")
            old = {"learner_id": "x", "nodes": {
                "K": {"id": "K", "knowledge_point": "K", "level": "2",
                      "error_types": {"语法-语序": 1}, "error_count": 1,
                      "mastery": 0, "last_learnt_at": None, "created_at": "t",
                      "error_kind": "语法", "nature": "错序"}},
                "edges": {}, "queue": {}, "_seen_events": []}
            with open(p, "w", encoding="utf-8") as f:
                json.dump(old, f, ensure_ascii=False)
            g = ErrorGraph("x")
            g.load(p)
            nd = g.get_kp("K")["node"]
            self.assertEqual(nd["unfixed_streak"], 0)
            self.assertEqual(nd["nature"], "错序")


if __name__ == "__main__":
    unittest.main()
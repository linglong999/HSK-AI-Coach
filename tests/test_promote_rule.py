# -*- coding: utf-8 -*-
"""B4 H4 · 构式晋升 + learned 迁移（现算不落盘，与 fossilized 同构）

promoted：positive_count≥3 且 unfixed_streak==0
learned（H4-2，B3 拍板2 阈值归 B4）：attempt_ok≥3 且无未纠正 attempt_err
判据表驱动 + to_dict 不落盘断言 + priority 加权（PROMOTE_BOOST）
运行: python -m unittest tests.test_promote_rule tests.test_learned_rule -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.graph.error_graph import (ErrorGraph, PROMOTE_BOOST, PROMOTE_RULE,
                                      FOSSIL_BOOST)
from engine.graph.model import Node


def _ts(days_ago: float) -> str:
    t = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class PromoteRuleTest(unittest.TestCase):

    def test_promote_positive3_streak0(self):
        g = ErrorGraph("p1")
        n = Node(id="X", knowledge_point="X", level="HSK1",
                 positive_count=3, unfixed_streak=0)
        g._refresh_promoted(n)
        self.assertTrue(n.promoted)

    def test_promote_positive3_streak1(self):
        g = ErrorGraph("p2")
        n = Node(id="X", knowledge_point="X", level="HSK1",
                 positive_count=3, unfixed_streak=1)
        g._refresh_promoted(n)
        self.assertFalse(n.promoted)   # 近期有未纠正 → 不晋升

    def test_promote_positive2(self):
        g = ErrorGraph("p3")
        n = Node(id="X", knowledge_point="X", level="HSK1",
                 positive_count=2, unfixed_streak=0)
        g._refresh_promoted(n)
        self.assertFalse(n.promoted)   # 未达复现阈值

    def test_promote_not_persisted(self):
        # 现算不落盘：to_dict 不含 promoted 键
        g = ErrorGraph("p4")
        n = Node(id="X", knowledge_point="X", level="HSK1",
                 positive_count=3, unfixed_streak=0)
        g._refresh_promoted(n)
        self.assertNotIn("promoted", n.to_dict())

    def test_priority_promote_boost(self):
        g = ErrorGraph("p5")
        n = Node(id="X", knowledge_point="X", level="HSK1",
                 error_count=2, mastery=0.0, nature="",
                 positive_count=3, unfixed_streak=0,
                 created_at=_ts(days_ago=5))
        base = g._priority(n)
        # 晋升态 → base * PROMOTE_BOOST（nature="" → 1.0，无叠乘混淆）
        g2 = ErrorGraph("p6")
        n2 = Node(id="Y", knowledge_point="Y", level="HSK1",
                  error_count=2, mastery=0.0, nature="",
                  positive_count=0, unfixed_streak=0,
                  created_at=_ts(days_ago=5))
        self.assertAlmostEqual(base, g2._priority(n2) * PROMOTE_BOOST, places=5)

    def test_fossil_precedes_promote(self):
        # 化石态（unfixed_streak≥3）即使正向也达标 → priority 走 FOSSIL_BOOST（互斥分支 fossil 在前）
        g = ErrorGraph("p7")
        n = Node(id="X", knowledge_point="X", level="HSK1",
                 error_count=2, mastery=0.0, nature="",
                 positive_count=3, unfixed_streak=5,
                 last_learnt_at=_ts(days_ago=200), created_at=_ts(days_ago=400))
        g._refresh_fossilized(n)
        g._refresh_promoted(n)
        self.assertTrue(n.fossilized)          # 化石：streak 5 ≥ 3 且闲置 ≥180 天
        self.assertFalse(n.promoted)           # streak!=0 → 不晋升（判据互斥）
        # FOSSIL_BOOST 生效：对照同 aging 未化石节点（同 last_learnt_at、streak=0）
        n2 = Node(id="Y", knowledge_point="Y", level="HSK1",
                  error_count=2, mastery=0.0, nature="",
                  positive_count=0, unfixed_streak=0,
                  last_learnt_at=_ts(days_ago=200), created_at=_ts(days_ago=400))
        base2 = g._priority(n2)
        self.assertAlmostEqual(g._priority(n), base2 * FOSSIL_BOOST, places=5)


class LearnedRuleTest(unittest.TestCase):

    def _mk(self, ok=0, err=0):
        n = Node(id="X", knowledge_point="X", level="HSK1")
        if ok:
            n.attempt_ok_reps = ok
        if err:
            n.attempt_err_unresolved = err
        return n

    def test_learned_ok3_no_err(self):
        g = ErrorGraph("l1")
        n = self._mk(ok=3)
        g._refresh_learned(n)
        self.assertTrue(n.learned)

    def test_learned_ok3_has_unresolved_err(self):
        g = ErrorGraph("l2")
        n = self._mk(ok=3, err=1)
        g._refresh_learned(n)
        self.assertFalse(n.learned)   # 期间有未纠正 attempt_err → 不迁 learned

    def test_learned_ok2(self):
        g = ErrorGraph("l3")
        n = self._mk(ok=2)
        g._refresh_learned(n)
        self.assertFalse(n.learned)

    def test_learned_missing_fields_defaults_not_migrated(self):
        # B3 未落地前缺字段 → 容错 0，不误判 learned
        g = ErrorGraph("l4")
        n = Node(id="X", knowledge_point="X", level="HSK1")
        g._refresh_learned(n)
        self.assertFalse(n.learned)

    def test_learned_not_persisted(self):
        g = ErrorGraph("l5")
        n = self._mk(ok=3)
        g._refresh_learned(n)
        self.assertTrue(n.learned)
        self.assertNotIn("learned", n.to_dict())
        self.assertNotIn("attempt_ok_reps", n.to_dict())
        self.assertNotIn("attempt_err_unresolved", n.to_dict())


if __name__ == "__main__":
    unittest.main()
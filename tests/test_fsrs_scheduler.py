# -*- coding: utf-8 -*-
"""P0.17 间隔调度 —— ErrorGraph 集成测试（B4：内核换 py-fsrs 适配器后保留调用点回归）。

覆盖 v1 §P0.17 / v2 间隔调度（自实现 fsrs 数学单测已迁 test_fsrs_adapter.py）：
  - 通过/失败后 S/D/next_review_at 变化符合 FSRS 规则（连续答对拉长、答错重置变短）
  - 冷启动缺省（无历史 → init_state + 保守首轮 ≥1 天）
  - due_nodes 严格到期口径；冷启动不进 due，走 unscheduled_topn（boost）
  - review_feedback：rating 直传优先、correct 兜底；streak 规则 rating==1→+1 else 0
  - ingest_verdict 不驱动 FSRS（唯一写入方=review_feedback）
运行: python -m unittest tests.test_fsrs_scheduler -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.scheduler.fsrs_adapter import FsrsSchedulerAdapter
from engine.graph.error_graph import ErrorGraph

# 与 error_graph 模块级 _FSRS 同一语义的内核实例，用于期望值重算（非死写数值）
_ADAPTER = FsrsSchedulerAdapter()


def _ts(days_ago: float) -> str:
    t = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------- ErrorGraph 集成 ----------------

class DueScheduleTest(unittest.TestCase):

    def _mk_due(self, kp_id, next_review_at, error_count=2):
        from engine.graph.error_graph import Node
        return Node(id=kp_id, knowledge_point=kp_id, level="HSK1",
                    error_count=error_count, next_review_at=next_review_at)

    def test_due_nodes_strict_past_equals_now(self):
        g = ErrorGraph("due")
        g._nodes = {
            "past": self._mk_due("past", _ts(days_ago=1)),
            "now_eq": self._mk_due("now_eq", _ts(days_ago=0)),
            "future": self._mk_due("future", _ts(days_ago=-3)),
            "none": self._mk_due("none", None),
        }
        due = g.due_nodes()
        ids = {r["kp_id"] for r in due}
        # 严格口径：到期(过去) + 恰好现在 进；未来/未调度(None) 不进
        self.assertIn("past", ids)
        self.assertIn("now_eq", ids)
        self.assertNotIn("future", ids)
        self.assertNotIn("none", ids)

    def test_due_sorted_by_priority_desc(self):
        g = ErrorGraph("duesort")
        g._nodes = {
            "low": self._mk_due("low", _ts(days_ago=1), error_count=1),
            "high": self._mk_due("high", _ts(days_ago=1), error_count=5),
        }
        due = g.due_nodes()
        self.assertEqual(due[0]["kp_id"], "high")  # 错误多 → priority 高 → 排前

    def test_cold_start_goes_to_boost_not_due(self):
        g = ErrorGraph("boost")
        g._nodes = {
            "cold": self._mk_due("cold", None, error_count=4),
            "scheduled": self._mk_due("scheduled", _ts(days_ago=1), error_count=2),
        }
        self.assertEqual(len(g.due_nodes()), 1)              # 冷启动不进 due
        top = g.unscheduled_topn(5)
        self.assertEqual([r["kp_id"] for r in top], ["cold"])  # boost 拿到冷启动


class ReviewFeedbackFsrsTest(unittest.TestCase):

    def test_feedback_writes_fsrs_and_next_review(self):
        g = ErrorGraph("rf")
        from engine.graph.error_graph import Node
        g._nodes = {"K": Node(id="K", knowledge_point="K", level="HSK1",
                              next_review_at=_ts(days_ago=1))}
        out = g.review_feedback("K", rating=3, event_key="r1")
        nd = out["node"]
        self.assertGreater(nd["fsrs_stability"], 0)
        self.assertIsNotNone(nd["last_review_at"])
        self.assertIsNotNone(nd["next_review_at"])
        # 首轮保守：间隔 ≥1 天（下限 max(1, round(...)) 保证不进 0）
        self.assertGreaterEqual(out["interval_days"], 1)
        # 冷启动 init_state(3) → 由适配器内核推出（关系断言而非死写数值，规避 FSRS-5→6 漂移）
        expect = round(_ADAPTER.interval(_ADAPTER.init_state(3).stability))
        self.assertEqual(out["interval_days"], max(1, expect))

    def test_rating1_forget_increments_streak(self):
        g = ErrorGraph("rf2")
        from engine.graph.error_graph import Node
        g._nodes = {"K": Node(id="K", knowledge_point="K", level="HSK1",
                              unfixed_streak=0)}
        g.review_feedback("K", rating=1, event_key="a")
        self.assertEqual(g.get_kp("K")["node"]["unfixed_streak"], 1)

    def test_rating3_remembers_resets_streak(self):
        g = ErrorGraph("rf3")
        from engine.graph.error_graph import Node
        g._nodes = {"K": Node(id="K", knowledge_point="K", level="HSK1",
                              unfixed_streak=3)}
        g.review_feedback("K", rating=3, event_key="b")
        self.assertEqual(g.get_kp("K")["node"]["unfixed_streak"], 0)

    def test_correct_bool_direct_map(self):
        # correct 兜底：true→3(想起) / false→1(忘记)
        g = ErrorGraph("rf4")
        from engine.graph.error_graph import Node
        g._nodes = {"K": Node(id="K", knowledge_point="K", level="HSK1")}
        g.review_feedback("K", correct=True, event_key="c1")
        g.review_feedback("K", correct=False, event_key="c2")
        # false→rating1 使 streak+1；true→rating3 使 streak 归 0，结尾 streak=1
        self.assertEqual(g.get_kp("K")["node"]["unfixed_streak"], 1)

    def test_ingest_verdict_does_not_drive_fsrs(self):
        # 边界：ingest_verdict 不写 next_review_at/fsrs（唯一写入方=review_feedback）
        g = ErrorGraph("rf5")
        bias = {"fragment": "f", "type": "语法", "knowledge_point_id": "K",
                "knowledge_point_name": "K", "level": "HSK1"}
        g.ingest_error(bias, event_key="e0")
        g.ingest_verdict(dict(bias), verdict="pass", uncertain=False, event_key="k0")
        nd = g.get_kp("K")["node"]
        self.assertIsNone(nd["next_review_at"])
        self.assertEqual(nd["fsrs_stability"], 0)


if __name__ == "__main__":
    unittest.main()
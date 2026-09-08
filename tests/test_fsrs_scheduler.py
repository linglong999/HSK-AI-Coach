# -*- coding: utf-8 -*-
"""P0.17 间隔调度（FSRS）—— 单元 + ErrorGraph 集成测试。

覆盖 v1 §P0.17 / v2 间隔调度：
  - fsrs 四函数（retrievability/init_state/next_state/interval）+ W 参数
  - 通过/失败后 S/D/next_review_at 变化符合 FSRS 规则（连续答对拉长、答错重置变短）
  - 冷启动缺省（无历史 → init_state + 保守首轮 ≥1 天）
  - due_nodes 严格到期口径；冷启动不进 due，走 unscheduled_topn（boost）
  - review_feedback：rating 直传优先、correct 兜底；streak 规则 rating==1→+1 else 0
  - ingest_verdict 不驱动 FSRS（唯一写入方=review_feedback）
运行: python -m unittest tests.test_fsrs_scheduler -v
"""
import math
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.scheduler import fsrs
from engine.graph.error_graph import ErrorGraph


def _ts(days_ago: float) -> str:
    t = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------- fsrs 模块单元 ----------------

class FsrsMathTest(unittest.TestCase):

    def test_retrievability_range(self):
        # R∈(0,1]；t=0 时 R=1；t 越大 R 越小
        self.assertAlmostEqual(fsrs.retrievability(1.0, 0.0), 1.0, places=6)
        r_now = fsrs.retrievability(5.0, 0.0)
        r_later = fsrs.retrievability(5.0, 10.0)
        self.assertGreater(r_now, r_later)

    def test_w_params_match_official(self):
        # W 与 py-fsrs v5.1.3 官方 FSRS-5 记忆参考值逐值核对（用户已 19/19 核过）
        expect = [0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604,
                  0.0046, 1.54575, 0.1192, 1.01925, 1.9395, 0.11, 0.29605,
                  2.2698, 0.2315, 2.9898, 0.51655, 0.6621, 0.0, 0.0]
        self.assertEqual(len(fsrs.W), 21)
        for i in range(21):
            self.assertAlmostEqual(fsrs.W[i], expect[i], places=4, msg=f"W[{i}]")
        self.assertAlmostEqual(fsrs.DECAY, -0.5)
        self.assertAlmostEqual(fsrs.FACTOR, 19.0 / 81.0)

    def test_init_state_rating_ladder(self):
        # 首次：更高评级 → 更高 S0；D0 在 [1,10] 且随评级升高
        s1 = fsrs.init_state(1).stability
        s4 = fsrs.init_state(4).stability
        self.assertGreater(s4, s1)
        for r in (1, 2, 3, 4):
            st = fsrs.init_state(r)
            self.assertGreaterEqual(st.difficulty, 1.0)
            self.assertLessEqual(st.difficulty, 10.0)

    def test_next_state_repeat_extends_interval(self):
        # 连续想起(3) → S 递增 → interval 拉长
        st = fsrs.init_state(3)
        i0 = fsrs.interval(st.stability)
        st2 = fsrs.next_state(st, 3, elapsed_days=1.0)
        i1 = fsrs.interval(st2.stability)
        self.assertGreater(st2.stability, st.stability)
        self.assertGreater(i1, i0)

    def test_next_state_forget_resets_interval(self):
        # 答错(1) → 遗忘，S 重回更低 → interval 变短
        st = fsrs.init_state(3)
        st2 = fsrs.next_state(st, 3, elapsed_days=1.0)
        st3 = fsrs.next_state(st2, 1, elapsed_days=20.0)
        self.assertLessEqual(st3.stability, st2.stability)  # 遗忘后 S 不高于旧
        self.assertLess(fsrs.interval(st3.stability), fsrs.interval(st2.stability))


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
        # 冷启动 init_state(3) → interval≈3.17 → round=3；断言它由 S0 自然推出（而非死写 1）
        import engine.scheduler.fsrs as fs
        expect = round(fs.interval(fs.init_state(3).stability))
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
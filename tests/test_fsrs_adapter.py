# -*- coding: utf-8 -*-
# tests/test_fsrs_adapter.py
# B4 · FsrsSchedulerAdapter 纯内核单测（不起图谱/存储）。
# 覆盖（实施计划 B4 H6）：
#   - 四 API 语义：init_state 首评 S/D 合理域、next_state rating 单调、interval 与 due 一致
#   - 评级 1-4 ↔ py-fsrs Rating 对齐（Again/Hard/Good/Easy == 1/2/3/4）
#   - FSRS-5→6 漂移用关系断言（无数值快照）
#   - MemoryState 字段保持（stability/difficulty）
# 运行: python -m unittest tests.test_fsrs_adapter -v

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fsrs import Rating, State
from engine.scheduler.fsrs_adapter import FsrsSchedulerAdapter, MemoryState


def _adapter():
    return FsrsSchedulerAdapter()   # desired_retention=0.9


class MemoryStateContractTest(unittest.TestCase):

    def test_fields_preserved(self):
        s = MemoryState()
        self.assertAlmostEqual(s.stability, 0.0)
        self.assertAlmostEqual(s.difficulty, 5.0)


class InitStateTest(unittest.TestCase):

    def test_first_review_s_d_plausible(self):
        a = _adapter()
        for r in (1, 2, 3, 4):
            st = a.init_state(r)
            self.assertGreater(st.stability, 0, f"r={r}")
            self.assertGreaterEqual(st.difficulty, 1.0, f"r={r}")
            self.assertLessEqual(st.difficulty, 10.0, f"r={r}")

    def test_rating_ladder(self):
        # 首次：更高评级 → 更高 S0（关系断言，非数值）
        a = _adapter()
        s1 = a.init_state(1).stability
        s4 = a.init_state(4).stability
        self.assertGreater(s4, s1)


class NextStateTest(unittest.TestCase):

    def test_review_state_goes_real_interval_path(self):
        # 成型卡必须走 State.Review（Learning 会被学习步骤困住）→ 复习后 S 明显抬升
        a = _adapter()
        init = a.init_state(3)
        nxt = a.next_state(init, 3, elapsed_days=3.0)
        self.assertGreater(nxt.stability, init.stability)
        self.assertGreater(nxt.difficulty, 0)

    def test_rating_monotonic(self):
        # 同一卡的 rating 越高 S 越大（Good>Hard-ish 关系断言）
        a = _adapter()
        base = a.init_state(3)
        s_easy = a.next_state(base, 4, elapsed_days=1.0).stability
        s_forget = a.next_state(base, 1, elapsed_days=1.0).stability
        self.assertGreater(s_easy, s_forget)

    def test_next_state_is_deterministic(self):
        # 两连调用同参数 → 相同 S（enable_fuzzing=False 的铺底：interval 确定性才成立）
        a = _adapter()
        base = a.init_state(3)
        st1 = a.next_state(base, 3, elapsed_days=5.0)
        st2 = a.next_state(base, 3, elapsed_days=5.0)
        self.assertAlmostEqual(st1.stability, st2.stability, places=6)


class IntervalTest(unittest.TestCase):

    def test_interval_matches_scheduled_due(self):
        # interval()（镜像公式）应等于库内单次复习推算出的到期间隔（同卡同 rating）
        a = _adapter()
        now = datetime.now(timezone.utc)
        from fsrs import Card
        card, _ = a._sched.review_card(
            Card(stability=a.init_state(3).stability,
                 difficulty=a.init_state(3).difficulty,
                 last_review=now - timedelta(days=3), state=State.Review),
            Rating.Good)
        scheduled = (card.due - card.last_review).total_seconds() / 86400.0
        # interval() 返回浮点基值；py-fsrs 落 due 时四舍五入为整数天 → 取整对齐
        self.assertEqual(round(a.interval(card.stability)), scheduled)

    def test_default_retention_used_when_r_omitted(self):
        a = _adapter()
        iv = a.interval(5.0)
        iv_explicit09 = a.interval(5.0, r=0.9)
        self.assertAlmostEqual(iv, iv_explicit09, places=6)


class RatingEnumAlignmentTest(unittest.TestCase):

    def test_rating_values_1_4(self):
        # 自实现 1忘记/2困难/3想起/4轻松 ↔ py-fsrs Again/Hard/Good/Easy
        self.assertEqual(Rating.Again.value, 1)
        self.assertEqual(Rating.Hard.value, 2)
        self.assertEqual(Rating.Good.value, 3)
        self.assertEqual(Rating.Easy.value, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
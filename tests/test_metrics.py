# -*- coding: utf-8 -*-
# tests/test_metrics.py
# P0.18 · 效果度量 4 指标单元测试
# - synthetic 夹具在 setUpClass 用临时目录生成（不污染 data/），跑完即清
# - 关键断言：无数据如实报 None（不编数）；synthetic 带通过轨迹时指标 1/2 可算
import json
import os
import tempfile
import unittest
import datetime

from engine.metrics import (
    pass_rate_delta, interactions_to_pass, continue_learning_rate,
    satisfaction, summary, all_metrics, record_feedback, iter_learners,
)

DAY = 86400
# 基准取"确定日正午"，保证会话结束日跨天、续学判断不受午夜时区抖动
BASE = datetime.datetime(2026, 9, 1, 12, 0, 0).timestamp()


def _w(root, name, obj):
    with open(os.path.join(root, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _build_synthetic(root):
    """造一份带通过轨迹 + 评分 的 synthetic learner（syn_pass）。"""
    T = BASE
    _w(root, "ledger_syn_pass.json", {
        "learner_id": "syn_pass",
        "events": [
            {"id": "e1", "kind": "observation_error", "kp_id": "kp-a",
             "ts": T - 10 * DAY},
            {"id": "e2", "kind": "repeated_error", "kp_id": "kp-a",
             "ts": T - 5 * DAY},
            {"id": "e3", "kind": "observation_error", "kp_id": "kp-a",
             "ts": T - 1 * DAY},
            {"id": "e4", "kind": "repeated_error", "kp_id": "kp-a",
             "ts": T + 2 * DAY},
            # kp-b：只有 through-pass 而无 error（测试 indicator1/2 的剔除路径）
        ],
    })
    _w(root, "graph_syn_pass.json", {
        "learner_id": "syn_pass",
        "nodes": {
            "kp-a": {"id": "kp-a", "positive_count": 2,
                     "last_positive_at": T, "positive_sources": {"复述": 2}},
            "kp-b": {"id": "kp-b", "positive_count": 1,
                     "last_positive_at": T + 7200},
        },
    })
    _w(root, "memory_syn_pass.json", {
        "learner_id": "syn_pass",
        "sessions": {
            # s1 结束(8/30 正午+15min)后 24h 内 s2 开始(8/31 正午) → 续学 true
            "s1": {"created_at": BASE - 2 * DAY, "updated_at": BASE - 2 * DAY + 900},
            # s2 结束(8/31 正午+15min)后无新会话 → 不续
            "s2": {"created_at": BASE - DAY, "updated_at": BASE - DAY + 900},
        },
    })
    _w(root, "feedback_syn_pass.json", {
        "learner_id": "syn_pass",
        "scores": [
            {"score": 5, "conversation_id": "c1", "ts": BASE},
            {"score": 4, "conversation_id": "c2", "ts": BASE + 60},
        ],
    })
    # 带通过轨迹、但通过早于首错 → 指标 2 应剔除（skipped）
    _w(root, "ledger_syn_late.json", {
        "learner_id": "syn_late",
        "events": [
            {"id": "x1", "kind": "observation_error", "kp_id": "kp-a",
             "ts": T + 10 * DAY},
        ],
    })
    _w(root, "graph_syn_late.json", {
        "learner_id": "syn_late",
        "nodes": {"kp-a": {"id": "kp-a", "positive_count": 1,
                           "last_positive_at": T + 5 * DAY}},
    })


class MetricsNoFabricationTest(unittest.TestCase):
    """现有 demo 数据：无通过/无评分 → 如实报 None，绝不编数。"""

    @classmethod
    def setUpClass(cls):
        cls.root = os.path.abspath("data")

    def test_demo_pass_delta_is_none(self):
        m = pass_rate_delta("demo", self.root)
        self.assertIsNone(m["value"])
        self.assertEqual(m["n"], 0)

    def test_demo_interactions_is_none(self):
        self.assertIsNone(interactions_to_pass("demo", self.root)["value"])

    def test_demo_satisfaction_is_none(self):
        self.assertIsNone(satisfaction("demo", self.root)["value"])


class MetricsSyntheticTest(unittest.TestCase):
    """synthetic 带通过轨迹：指标 1/2/3/4 都能算出数字。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="p018_")
        _build_synthetic(cls.tmp)
        cls.root = cls.tmp

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_iter_learners_discovers_synthetic(self):
        self.assertIn("syn_pass", iter_learners(self.root))
        self.assertIn("syn_late", iter_learners(self.root))

    def test_pass_rate_delta_negative_progress(self):
        # kp-a：10d 前 3 错 / 14d 后 1 错 → after-before < 0（进步）
        m = pass_rate_delta("syn_pass", self.root)
        self.assertIsNotNone(m["value"])
        self.assertLess(m["value"], 0)
        self.assertEqual(m["n"], 1)   # kp-b 通过前无错 → 剔除为 no_before

    def test_interactions_to_pass_rounds(self):
        # kp-a：首错(T-10d)到首通过(T) 之间 3 条事件
        m = interactions_to_pass("syn_pass", self.root)
        self.assertIsNotNone(m["value"])
        self.assertEqual(m["n"], 1)
        self.assertEqual(m["detail"][0]["rounds"], 3)

    def test_late_pass_excluded(self):
        # syn_late：首错晚于首通过 → 指标 2 无该 kp，值为 None
        m = interactions_to_pass("syn_late", self.root)
        self.assertIsNone(m["value"])

    def test_continue_learning_rate(self):
        m = continue_learning_rate("syn_pass", self.root)
        self.assertIsNotNone(m["value"])
        self.assertEqual(m["n"], 2)   # s1、s2 两个结束日
        self.assertEqual(m["value"], 0.5)  # 仅 s2 结束后续学

    def test_satisfaction_mean(self):
        m = satisfaction("syn_pass", self.root)
        self.assertEqual(m["n"], 2)
        self.assertEqual(m["value"], 4.5)

    def test_summary_nonempty_and_mentions_learner(self):
        s = summary("syn_pass", self.root)
        self.assertIn("syn_pass", s)
        self.assertIn("效果度量", s)

    def test_all_metrics_dict(self):
        am = all_metrics(self.root)
        self.assertIn("syn_pass", am["learners"])
        for k in ("pass_rate_delta", "interactions_to_pass",
                  "continue_learning_rate", "satisfaction"):
            self.assertIn(k, am["learners"]["syn_pass"])


class FeedbackRecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p018fb_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_and_readback(self):
        self.assertTrue(record_feedback(self.tmp, "fb_user", 5, conversation_id="c9"))
        self.assertTrue(record_feedback(self.tmp, "fb_user", 3))
        m = satisfaction("fb_user", self.tmp)
        self.assertEqual(m["n"], 2)
        self.assertEqual(m["value"], 4.0)

    def test_invalid_score_rejected(self):
        self.assertFalse(record_feedback(self.tmp, "fb_user", 0))
        self.assertFalse(record_feedback(self.tmp, "fb_user", 6))
        self.assertFalse(record_feedback(self.tmp, "fb_user", "abc"))
        self.assertIsNone(satisfaction("fb_user", self.tmp)["value"])  # 拒绝后不落盘


if __name__ == "__main__":
    unittest.main()
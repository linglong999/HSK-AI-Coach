# -*- coding: utf-8 -*-
# tests/test_error_graph_alignment.py —— ErrorGraph 行为对齐基线（深化批次A）
# 在未改动的 ErrorGraph 上锁死现状行为，作为"拆成 model/store/service"的回归裁判：
# 任何拆分若改变 shape/语义，此测试立即红。覆盖：data模型字段、写回语义
# （幂等/streak/fossilized）、查询组装（priority 现算）、FSRS、快照、持久化 roundtrip。
# 全部用临时目录，避免写 data/。

import json
import os
import shutil
import tempfile
import unittest

from engine.graph.error_graph import ErrorGraph

BIAS = {
    "fragment": "苹果很多", "correction": "很多苹果",
    "type": "语序-定语后置", "type_confident": True, "confidence": 0.9,
    "knowledge_point_id": "kp-a", "uncertain": False,
    "level": "HSK3", "knowledge_point_name": "定语后置",
}


class AlignmentBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hsk_graph_align_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.g = ErrorGraph("align")

    def tearDown(self):
        # 不用真实持久化路径，清空内存即可
        pass


class IngestTest(AlignmentBase):
    def test_ingest_confirmed_upserts_node(self):
        out = self.g.ingest_error(BIAS, event_key="e1")
        self.assertEqual(out["status"], "node_upsert")
        self.assertEqual(out["kp_id"], "kp-a")
        node = out["node"]
        self.assertEqual(node["knowledge_point"], "定语后置")
        self.assertEqual(node["level"], "HSK3")
        # 新错误 mastery = 0.0 * 0.5 = 0.0（MASTERY_NEW_ERROR 衰减，起点即 0）
        self.assertEqual(node["mastery"], 0.0)
        self.assertEqual(node["unfixed_streak"], 0)      # 新建归零
        self.assertIn(node["error_kind"], ("未知", "语法"))  # 维度自动回填（kp-a 未映射→未知）

    def test_ingest_idempotent_replay(self):
        self.g.ingest_error(BIAS, event_key="e1")
        out = self.g.ingest_error(BIAS, event_key="e1")
        self.assertEqual(out["status"], "idempotent_skip")
        self.assertEqual(self.g.__len__(), 1)
        self.assertEqual(self.g.get_kp("kp-a")["node"]["error_count"], 1)

    def test_ingest_count_accumulates_distinct_events(self):
        self.g.ingest_error(BIAS, event_key="e1")
        self.g.ingest_error(BIAS, event_key="e2")
        self.assertEqual(self.g.get_kp("kp-a")["node"]["error_count"], 2)
        # 二次错误 mastery 保持 0.0（已为 0，衰减仍 0）
        self.assertEqual(self.g.get_kp("kp-a")["node"]["mastery"], 0.0)

    def test_ingest_uncertain_goes_to_queue(self):
        b = dict(BIAS, uncertain=True)
        out = self.g.ingest_error(b, event_key="e1")
        self.assertEqual(out["status"], "to_queue")
        self.assertEqual(self.g.__len__(), 0)            # 不进节点
        self.assertIsNone(self.g.get_kp("kp-a"))

    def test_ingest_kp_miss_goes_to_pending_mapping(self):
        b = dict(BIAS, knowledge_point_id="", uncertain=False)
        out = self.g.ingest_error(b, event_key="e1")
        self.assertEqual(out["status"], "to_queue")
        self.assertEqual(out["item_key"], "苹果很多|语序-定语后置|")


class VerdictTest(AlignmentBase):
    def _seed(self):
        self.g.ingest_error(BIAS, event_key="e1")

    def test_verdict_pass_raises_mastery_and_zeroes_streak(self):
        self._seed()
        self.g.review_feedback("kp-a", rating=1, event_key="r1")   # 先制造 streak=1
        self.assertEqual(self.g.get_kp("kp-a")["node"]["unfixed_streak"], 1)
        out = self.g.ingest_verdict(BIAS, "pass", uncertain=False, event_key="v1")
        self.assertEqual(out["status"], "node_update")
        node = self.g.get_kp("kp-a")["node"]
        self.assertEqual(node["unfixed_streak"], 0)
        self.assertGreater(node["mastery"], 0.0)          # pass 提升（0 → 0.5）
        self.assertIsNotNone(node["last_learnt_at"])      # pass touch

    def test_verdict_fail_decays_but_keeps_last_learnt(self):
        self._seed()
        learnt = self.g.get_kp("kp-a")["node"]["last_learnt_at"]
        self.g.ingest_verdict(BIAS, "fail", uncertain=False, event_key="v1")
        self.g.ingest_verdict(BIAS, "fail", uncertain=False, event_key="v2")
        node = self.g.get_kp("kp-a")["node"]
        self.assertEqual(node["unfixed_streak"], 2)
        self.assertLess(node["mastery"], 0.5)             # fail 衰减
        self.assertEqual(node["last_learnt_at"], learnt)  # fail 不 touch

    def test_verdict_idempotent(self):
        self._seed()
        self.g.ingest_verdict(BIAS, "pass", uncertain=False, event_key="v1")
        m1 = self.g.get_kp("kp-a")["node"]["mastery"]
        out = self.g.ingest_verdict(BIAS, "pass", uncertain=False, event_key="v1")
        self.assertEqual(out["status"], "idempotent_skip")
        self.assertEqual(self.g.get_kp("kp-a")["node"]["mastery"], m1)


class PositiveTest(AlignmentBase):
    def test_positive_not_touch_bias_side(self):
        self.g.ingest_error(BIAS, event_key="e1")
        g = self.g
        before = g.get_kp("kp-a")
        p = g.ingest_positive("kp-a", source="demo", event_key="p1")
        self.assertEqual(p["status"], "node_positive")
        node = g.get_kp("kp-a")["node"]
        self.assertEqual(node["positive_count"], 1)
        self.assertEqual(node["positive_sources"], {"demo": 1})
        # 偏误侧不变：error_count/mastery 都不动
        self.assertEqual(node["error_count"], before["node"]["error_count"])
        self.assertEqual(node["mastery"], before["node"]["mastery"])
        # priority 只看偏误侧，正向不改变
        self.assertEqual(g.get_kp("kp-a")["priority"], before["priority"])


class ReviewFeedbackTest(AlignmentBase):
    def _seed(self):
        self.g.ingest_error(BIAS, event_key="e1")

    def test_cold_start_init_state(self):
        self._seed()
        out = self.g.review_feedback("kp-a", rating=4, event_key="r1")
        self.assertEqual(out["status"], "review_feedback")
        self.assertEqual(out["rating"], 4)
        self.assertIsNotNone(out["node"]["next_review_at"])
        self.assertEqual(out["node"]["unfixed_streak"], 0)  # rating!=1

    def test_rating_one_increments_streak_and_new_node_no_fossil(self):
        self._seed()
        # 新节点 last_learnt_at=None → days_idle=0 → 永不误标化石（编码保护）
        for i in range(6):
            out = self.g.review_feedback("kp-a", rating=1, event_key=f"r{i}")
            self.assertFalse(out.get("fossilized"))
        node = self.g.get_kp("kp-a")["node"]
        self.assertEqual(node["unfixed_streak"], 6)
        # fossilized 现算、不落盘：node dict 永不含该键（fossilized 不序列化）
        self.assertNotIn("fossilized", node)

    def test_rating_three_zeroes_streak(self):
        self._seed()
        self.g.review_feedback("kp-a", rating=1, event_key="r1")
        self.assertEqual(self.g.get_kp("kp-a")["node"]["unfixed_streak"], 1)
        self.g.review_feedback("kp-a", rating=3, event_key="r2")
        self.assertEqual(self.g.get_kp("kp-a")["node"]["unfixed_streak"], 0)

    def test_review_does_not_touch_mastery(self):
        self._seed()
        m = self.g.get_kp("kp-a")["node"]["mastery"]
        self.g.review_feedback("kp-a", rating=4, event_key="r1")
        self.assertEqual(self.g.get_kp("kp-a")["node"]["mastery"], m)


class QueueAndConfirmTest(AlignmentBase):
    def test_confirm_item_promotes_to_node(self):
        b = dict(BIAS, uncertain=True)
        self.g.ingest_error(b, event_key="e1")
        out = self.g.confirm_item("苹果很多|语序-定语后置|kp-a", valid=True, applied_valid=True)
        self.assertEqual(out["status"], "confirmed")
        node = self.g.get_kp("kp-a")
        self.assertIsNotNone(node)

    def test_confirm_still_pending_when_not_pass(self):
        b = dict(BIAS, uncertain=True)
        self.g.ingest_error(b, event_key="e1")
        out = self.g.confirm_item("苹果很多|语序-定语后置|kp-a", valid=False)
        self.assertEqual(out["status"], "still_pending")
        self.assertIsNone(self.g.get_kp("kp-a"))

    def test_reject_removes(self):
        b = dict(BIAS, uncertain=True)
        self.g.ingest_error(b, event_key="e1")
        key = "苹果很多|语序-定语后置|kp-a"
        self.assertEqual(self.g.reject_item(key)["status"], "rejected")
        self.assertEqual(self.g.reject_item(key)["status"], "not_found")


class QueryTest(AlignmentBase):
    def test_review_queue_sorts_by_priority_and_excludes_pending(self):
        # kp-a 确认入图（2 偏误）；kp-b 走队列（未确认，应排除）
        self.g.ingest_error(BIAS, event_key="e1")
        self.g.ingest_error(BIAS, event_key="e2")
        self.g.ingest_error(dict(BIAS, knowledge_point_id="kp-b",
                                 uncertain=True), event_key="e3")
        queue = self.g.get_review_queue()
        ids = [r["kp_id"] for r in queue]
        self.assertIn("kp-a", ids)
        self.assertNotIn("kp-b", ids)      # 待确认排除
        # priority 降序
        prios = [r["priority"] for r in queue]
        self.assertEqual(prios, sorted(prios, reverse=True))

    def test_get_kp_priority_aging(self):
        self.g.ingest_positive("kp-x", source="demo", event_key="p1")
        node = self.g.get_kp("kp-x")
        self.assertIsNotNone(node)
        self.assertGreaterEqual(node["priority"], 0)


class PersistenceTest(AlignmentBase):
    def test_save_load_roundtrip(self):
        self.g.ingest_error(BIAS, event_key="e1")
        self.g.ingest_error(dict(BIAS, knowledge_point_id="kp-c"), event_key="e2")
        path = os.path.join(self.tmp, "graph.json")
        self.g.review_feedback("kp-a", rating=1, event_key="r1")  # streak=1 应落盘
        self.g.save(path)
        g2 = ErrorGraph("align")
        g2.load(path)
        self.assertEqual(g2.__len__(), 2)
        nd = g2.get_kp("kp-a")["node"]
        self.assertEqual(nd["unfixed_streak"], 1)
        self.assertEqual(nd["knowledge_point"], "定语后置")

    def test_load_missing_file_inert(self):
        g2 = ErrorGraph("align")
        g2.load(os.path.join(self.tmp, "nope.json"))
        self.assertEqual(g2.__len__(), 0)


class SnapshotTest(AlignmentBase):
    def test_snapshot_shape_and_pending_excluded(self):
        self.g.ingest_error(BIAS, event_key="e1")
        self.g.ingest_error(dict(BIAS, knowledge_point_id="kp-b",
                                 uncertain=True), event_key="e3")
        self.g.link_errors_in_sentence(["kp-a", "kp-b"], event_key="s1")
        snap = self.g.graph_snapshot()
        self.assertEqual(snap["learner_id"], "align")
        self.assertIn("kp-a", snap["nodes"])
        self.assertNotIn("kp-b", snap["nodes"])     # 待确认不进画布
        # 混淆边：kp-a 已确认，kp-b 未确认 → 边仍建（link 不做确认过滤）
        self.assertEqual(len(snap["edges"]), 1)
        # 复习队列含已确认的 kp-a，排除待确认的 kp-b
        self.assertEqual([q["kp_id"] for q in snap["queue"]], ["kp-a"])


if __name__ == "__main__":
    unittest.main()
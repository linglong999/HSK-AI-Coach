# -*- coding: utf-8 -*-
"""B3 G2/G4 schema 测试：Node 快照五字段 round-trip（含向后兼容默认值）+
messages.metadata_json 自评事件结构断言 + query_meta_events 聚合 +
avoidance_observed 账本 kind + 锚点自由文本/残缺事件被拒。"""
import json
import os
import tempfile
import unittest

from engine.graph.model import Node
from engine.memory import store
from engine.memory.error_ledger import (
    ErrorLedger, KINDS, query_meta_events)


class NodeSchemaTest(unittest.TestCase):
    def test_meta_fields_round_trip(self):
        n = Node(id="kp-ba", knowledge_point="把字句", level="HSK3",
                 meta_confidence="half", meta_anchor="kp-ba-1",
                 avoidance_state="avoided", avoidance_count=3,
                 last_avoidance_at="2026-09-23T10:00:00Z")
        d = n.to_dict()
        self.assertEqual(d["meta_confidence"], "half")
        self.assertEqual(d["meta_anchor"], "kp-ba-1")
        self.assertEqual(d["avoidance_state"], "avoided")
        self.assertEqual(d["avoidance_count"], 3)
        self.assertIn("last_avoidance_at", d)
        # round-trip：重新构造（from_dict 语义=读回 to_dict 键）
        d2 = Node(**d).to_dict()
        for k in ("meta_confidence", "meta_anchor", "avoidance_state",
                  "avoidance_count", "last_avoidance_at"):
            self.assertEqual(d[k], d2[k])

    def test_defaults_present_backward_compat(self):
        # 验收补充：加字段全默认值向后兼容（旧图谱 JSON 载入零迁移）
        n = Node(id="kp-ba", knowledge_point="把字句", level="HSK3")
        self.assertIsNone(n.meta_confidence)
        self.assertIsNone(n.meta_anchor)
        self.assertIsNone(n.avoidance_state)
        self.assertEqual(n.avoidance_count, 0)
        self.assertIsNone(n.last_avoidance_at)
        d = n.to_dict()
        self.assertIsNone(d["meta_confidence"])
        self.assertEqual(d["avoidance_count"], 0)

    def test_primary_key_unchanged(self):
        # 主键 knowledge_point 不变（§4a 一致）
        n = Node(id="kp-x", knowledge_point="量词", level="HSK2")
        self.assertEqual(n.id, "kp-x")
        self.assertTrue({k: v for k, v in n.to_dict().items()
                         if k in ("id", "knowledge_point")})


class MetaEventSchemaTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.conn = store.ensure_store(self.root)

    def tearDown(self):
        try:
            self._tmp.cleanup()
        except OSError:
            pass

    def _write_meta(self, learner="default", seq=1, **kv):
        self.conn.execute(
            "INSERT INTO messages (learner_id, session_id, seq, role,"
            " content, metadata_json, ts) VALUES (?,?,?,?,?,?,?)",
            (learner, "s1", seq, "assistant", "",
             json.dumps(kv, ensure_ascii=False), 1))
        self.conn.commit()

    def test_query_meta_events_pairs(self):
        for i, (lv, out) in enumerate([("certain", "correct"), ("half", "wrong"),
                                       ("uncertain", "correct")], start=1):
            self._write_meta(learner="u1", seq=i, meta_event="self_rating",
                             self_level=lv, outcome_at_time=out,
                             anchor="kp-ba")
        pairs = query_meta_events("u1", self.root)
        self.assertEqual(len(pairs), 3)
        self.assertIn(("certain", "correct"), pairs)
        self.assertIn(("half", "wrong"), pairs)

    def test_query_skips_non_rating_and_bad_fields(self):
        # 非 self_rating 事件 / 非法档 / 残缺事件：全部跳过（校验器语义）
        self._write_meta(learner="u2", seq=1, meta_event="other",
                         self_level="certain", outcome_at_time="correct")
        self._write_meta(learner="u2", seq=2, meta_event="self_rating",
                         self_level="sure", outcome_at_time="correct")
        self._write_meta(learner="u2", seq=3, meta_event="self_rating",
                         self_level="half")                       # 缺 outcome
        self.assertEqual(query_meta_events("u2", self.root), [])

    def test_anchor_standard_id_rounds(self):
        # 锚点需标准 id：合法 kp 前缀不被拒，自由文本事件不产生 pair 但仍可查
        self._write_meta(learner="u3", seq=1, meta_event="self_rating",
                         self_level="certain", outcome_at_time="correct",
                         anchor="kp-liangci-02")
        pairs = query_meta_events("u3", self.root)
        self.assertEqual(pairs, [("certain", "correct")])

    def test_query_missing_learner_returns_empty(self):
        self.assertEqual(query_meta_events("nobody", self.root), [])

    def test_query_no_db_default_root_returns_empty(self):
        # 网络/库不可用 → 空列表，不炸（不传默认 root，避免触碰真实 data）
        self.assertEqual(query_meta_events("default", "::no_such_root::"), [])


class AvoidanceLedgerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.ledger = ErrorLedger(learner_id="avd", root=self.root)

    def tearDown(self):
        try:
            self._tmp.cleanup()
        except OSError:
            pass

    def test_avoidance_kind_registered(self):
        self.assertIn("avoidance_observed", KINDS)

    def test_observe_avoidance_records(self):
        eid = self.ledger.observe_avoidance("kp-ba", "avoided",
                                            evidence="绕行：放书在桌子上")
        events = self.ledger.recent()
        self.assertTrue(any(e["kind"] == "avoidance_observed"
                            and e["kp_id"] == "kp-ba" for e in events))

    def test_avoidance_not_retagged_repeated(self):
        self.ledger.observe_avoidance("kp-ba", "avoided")
        self.ledger.observe_avoidance("kp-ba", "avoided")
        kinds = [e["kind"] for e in self.ledger.recent()]
        self.assertNotIn("repeated_error", kinds)
        self.assertEqual(kinds.count("avoidance_observed"), 2)


if __name__ == "__main__":
    unittest.main()
# -*- coding: utf-8 -*-
# tests/test_graph_store.py —— GraphStore（engine/graph/store.py）独立直测（深化批次D）
# 直接测 Repository 层：容器可写、save/load roundtrip、旧字段迁移回填
# （P0.2/P0.3/P0.4/P0.5）、缺文件惰性、learner_id 回读。全程临时目录，不写 data/。

import json
import os
import shutil
import tempfile
import unittest

from engine.graph.error_kind_map import resolve
from engine.graph.model import Edge, Node, QueueItem
from engine.graph.store import GraphStore


def make_node(nid="kp-a", **over):
    fields = dict(id=nid, knowledge_point="定语后置", level="HSK3")
    fields.update(over)
    return Node(**fields)


class StoreContainerTest(unittest.TestCase):
    def test_containers_writable_plain_attrs(self):
        s = GraphStore()
        s.nodes["kp-a"] = make_node()
        s.edges["a|b"] = Edge("a", "b")
        s.queue["q"] = QueueItem(item_key="q", signature={}, status="pending")
        s.seen_events.add("e1")
        self.assertEqual(s.nodes["kp-a"].id, "kp-a")
        self.assertEqual(s.edges["a|b"].to_node_id, "b")
        self.assertEqual(s.queue["q"].status, "pending")
        self.assertIn("e1", s.seen_events)


class StorePersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hsk_store_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "graph.json")

    def test_save_load_roundtrip_preserves_all(self):
        s = GraphStore()
        n = make_node("kp-a", error_types={"语序-定语后置": 2}, error_count=2,
                      mastery=0.4, unfixed_streak=1,
                      fsrs_stability=3.2, fsrs_difficulty=6.1,
                      next_review_at="2026-10-01T00:00:00Z")
        s.nodes["kp-a"] = n
        s.edges["a|b"] = Edge("kp-a", "kp-b", relation="混淆",
                              edge_weight=3, event_keys=["e1"])
        s.queue["pending-x"] = QueueItem(item_key="pending-x",
                                         signature={"f": "f"}, status="pending",
                                         uncertain_src="识别待确认")
        s.seen_events.update(["e1", "e2"])
        s.save("alice", self.path)

        s2 = GraphStore()
        learner = s2.load("alice", self.path)
        self.assertEqual(learner, "alice")
        n2 = s2.nodes["kp-a"]
        self.assertEqual(n2.error_count, 2)
        self.assertEqual(n2.mastery, 0.4)
        self.assertEqual(n2.unfixed_streak, 1)
        self.assertEqual(n2.fsrs_stability, 3.2)
        self.assertEqual(n2.fsrs_difficulty, 6.1)
        self.assertEqual(n2.next_review_at, "2026-10-01T00:00:00Z")
        self.assertEqual(len(s2.edges), 1)
        self.assertEqual(s2.edges["a|b"].relation, "混淆")
        self.assertEqual(s2.edges["a|b"].edge_weight, 3)
        self.assertEqual(len(s2.queue), 1)
        self.assertEqual(s2.queue["pending-x"].status, "pending")
        self.assertEqual(s2.seen_events, {"e1", "e2"})

    def test_save_is_atomic_no_tmp_leftover(self):
        s = GraphStore()
        s.nodes["kp-a"] = make_node()
        s.save("alice", self.path)
        self.assertTrue(os.path.exists(self.path))
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        # 内容合法 JSON
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("kp-a", data["nodes"])

    def test_load_missing_file_inert_returns_default(self):
        s = GraphStore()
        learner = s.load("alice", os.path.join(self.tmp, "nope.json"))
        self.assertEqual(learner, "default")
        self.assertEqual(s.nodes, {})

    def test_load_uses_embedded_learner_id(self):
        s = GraphStore()
        s.nodes["kp-a"] = make_node()
        s.save("bob", self.path)
        # 改文件名携带的 learner 与内嵌不同，load 应回内嵌者
        s2 = GraphStore()
        learner = s2.load("whatever", self.path, default_learner="default")
        self.assertEqual(learner, "bob")


class StoreMigrationTest(unittest.TestCase):
    """旧 JSON（缺 P0.2/P0.3/P0.4 字段）load 时安全回填，不丢字段。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hsk_store_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "graph.json")

    def _write_legacy(self, nodes):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"nodes": nodes, "edges": {}, "queue": {}}, f)

    def test_legacy_missing_dual_fields_backfilled(self):
        # 旧数据缺 error_kind/nature/positive_*/unfixed_streak/last_positive_at
        self._write_legacy({
            "kp-a": {
                "id": "kp-a", "knowledge_point": "定语后置", "level": "HSK3",
                "error_types": {"语序-定语后置": 1}, "error_count": 1,
                "mastery": 0.0, "last_learnt_at": None, "created_at": "2026-01-01T00:00:00Z",
            }
        })
        s = GraphStore()
        s.load("legacy", self.path)
        n = s.nodes["kp-a"]
        # P0.2：error_kind/nature 现算补齐
        dims = resolve(n.error_types, n.id)
        self.assertEqual(n.error_kind, dims["error_kind"])
        self.assertEqual(n.nature, dims["nature"])
        # P0.3：正向字段安全缺省
        self.assertEqual(n.positive_count, 0)
        self.assertEqual(n.positive_sources, {})
        self.assertIsNone(n.last_positive_at)
        # P0.4：streak 缺省 0
        self.assertEqual(n.unfixed_streak, 0)
        # P0.5：created_at 保留旧值（aging 起算点不丢）
        self.assertEqual(n.created_at, "2026-01-01T00:00:00Z")

    def test_legacy_missing_created_at_defaults_empty(self):
        self._write_legacy({
            "kp-a": {"id": "kp-a", "knowledge_point": "x", "level": "HSK1",
                     "error_types": {}, "error_count": 0, "mastery": 0.0}
        })
        s = GraphStore()
        s.load("legacy", self.path)
        self.assertEqual(s.nodes["kp-a"].created_at, "")
        # 缺 error_kind/nature 时空 error_types 也走 resolve 现算（空→"未知"），不抛错
        self.assertEqual(s.nodes["kp-a"].error_kind, "未知")
        self.assertEqual(s.nodes["kp-a"].level, "HSK1")


if __name__ == "__main__":
    unittest.main()
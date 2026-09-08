# -*- coding: utf-8 -*-
"""P0.3 · 正向证据接口 ingest_positive 测试。
覆盖（用户 P0.3 拍板 + B 追加）：
  - 正向/偏误可区分：ingest_positive 不填 error_kind/nature（缺省""），
    正向证据只增 positive_count，不增 error_count
  - priority 不变性：ingest_positive 前后 priority 完全一致（只碰偏误侧）
  - positive_sources 按 {source: count} 聚合
  - 幂等：同一 event_key 不重复计数
  - last_positive_at 刷新；confidence 不持久化
  - 正向节点与偏误节点可在同一 kp 共存、不互相干扰
运行: python -m unittest tests.test_positive_ingest -v"""
import os
import sys
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.graph.error_graph import ErrorGraph


class IngestPositiveTest(unittest.TestCase):

    def _graph(self):
        return ErrorGraph("test_pos")

    def test_positive_does_not_set_dimensions(self):
        # 正向节点双维度保持缺省""（空制：nature空=非偏误信号）
        g = self._graph()
        g.ingest_positive("kp-liangci", source="scenario")
        nd = g.get_kp("kp-liangci")["node"]
        self.assertEqual(nd["error_kind"], "")
        self.assertEqual(nd["nature"], "")

    def test_positive_only_increments_positive_side(self):
        # 正向证据：positive_count+1，error_count/mastery/last_learnt 不动
        g = self._graph()
        g.ingest_error({"fragment": "我买一个奶茶", "type": "语法",
                        "knowledge_point_id": "kp-liangci"}, event_key="e-neg")
        node_before = g.get_kp("kp-liangci")["node"]
        err_before, mastery_before = node_before["error_count"], node_before["mastery"]

        g.ingest_positive("kp-liangci", source="correct", event_key="e-pos")
        node_after = g.get_kp("kp-liangci")["node"]
        self.assertEqual(node_after["positive_count"], 1)
        self.assertEqual(node_after["error_count"], err_before)
        self.assertEqual(node_after["mastery"], mastery_before)

    def test_priority_unchanged(self):
        # 关键锁死：正/负可共存，priority 只看偏误侧 → 正向后不变
        g = self._graph()
        g.ingest_error({"fragment": "x", "type": "语法",
                        "knowledge_point_id": "kp-liangci"}, event_key="e1")
        p_before = g.get_review_queue()  # 含 kp-liangci 的 priority
        prio_before = next(q["priority"] for q in p_before if q["kp_id"] == "kp-liangci")

        g.ingest_positive("kp-liangci", source="correct", event_key="e2")
        p_after = g.get_review_queue()
        prio_after = next(q["priority"] for q in p_after if q["kp_id"] == "kp-liangci")
        self.assertEqual(prio_after, prio_before)

    def test_positive_alone_has_zero_priority(self):
        # 纯正向节点（无任何偏误）priority=0，不污染排序
        g = self._graph()
        g.ingest_positive("kp-correct-only", source="scenario")
        queue = g.get_review_queue()
        item = next(q for q in queue if q["kp_id"] == "kp-correct-only")
        self.assertEqual(item["priority"], 0.0)

    def test_positive_sources_aggregates_by_source(self):
        # positive_sources 按 {source: count} 聚合
        g = self._graph()
        g.ingest_positive("kp-zhe", source="scenario", event_key="a")
        g.ingest_positive("kp-zhe", source="scenario", event_key="b")
        g.ingest_positive("kp-zhe", source="exercise", event_key="c")
        nd = g.get_kp("kp-zhe")["node"]
        self.assertEqual(nd["positive_count"], 3)
        self.assertEqual(nd["positive_sources"], {"scenario": 2, "exercise": 1})

    def test_idempotent_same_event_key(self):
        # 同一 event_key 重放 → 幂等跳，不重复计数
        g = self._graph()
        g.ingest_positive("kp-guo", source="correct", event_key="same")
        g.ingest_positive("kp-guo", source="correct", event_key="same")
        nd = g.get_kp("kp-guo")["node"]
        self.assertEqual(nd["positive_count"], 1)

    def test_last_positive_at_updated_and_confidence_not_persisted(self):
        # last_positive_at 刷新；confidence 不落盘（positive_sources 无 confidence 键）
        g = self._graph()
        g.ingest_positive("kp-de-di-de", source="writing", confidence=0.4, event_key="p")
        nd = g.get_kp("kp-de-di-de")["node"]
        self.assertIsNotNone(nd["last_positive_at"])
        self.assertNotIn("confidence", nd["positive_sources"])
        self.assertNotIn("confidence", nd)


class PositivePersistenceTest(unittest.TestCase):
    """save/load：正向字段不丢、旧数据缺省兜底。"""

    def _write_graph(self, path, nodes, extra=None):
        import json
        data = {"learner_id": "x", "nodes": nodes,
                "edges": {}, "queue": {}, "_seen_events": [],
                **(extra or {})}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return path

    def test_save_roundtrip_preserves_positive_fields(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "g.json")
            g = ErrorGraph("rt")
            g.ingest_positive("kp-zhuangyu-chezhi", source="exercise", event_key="e")
            g.save(p)
            g2 = ErrorGraph("rt")
            g2.load(p)
            nd = g2.get_kp("kp-zhuangyu-chezhi")["node"]
            self.assertEqual(nd["positive_count"], 1)
            self.assertEqual(nd["positive_sources"], {"exercise": 1})

    def test_save_roundtrip_preserves_created_at(self):
        # P0.5修复：created_at 进白名单，load→save 不再丢 aging 起算点
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "g.json")
            g = ErrorGraph("rt2")
            g.ingest_positive("kp-liangci", source="scenario", event_key="e")
            before = g.get_kp("kp-liangci")["node"]["created_at"]
            self.assertTrue(before)  # 有值（非空）
            g.save(p)
            g2 = ErrorGraph("rt2")
            g2.load(p)
            after = g2.get_kp("kp-liangci")["node"]["created_at"]
            self.assertEqual(after, before)  # 往返不丢

    def test_load_old_data_defaults_positive_fields(self):
        # 旧数据无正向字段 → 缺省 0/{} / None，不炸
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "g.json")
            self._write_graph(p, {
                "kp-liangci": {"id": "kp-liangci", "knowledge_point": "量词",
                               "level": "2", "error_types": {"语法": 2},
                               "error_count": 2, "mastery": 0.3,
                               "last_learnt_at": None, "created_at": "t",
                               "error_kind": "语法", "nature": "误代"},
            })
            g = ErrorGraph("x")
            g.load(p)
            nd = g.get_kp("kp-liangci")["node"]
            self.assertEqual(nd["positive_count"], 0)
            self.assertEqual(nd["positive_sources"], {})
            self.assertIsNone(nd["last_positive_at"])


if __name__ == "__main__":
    unittest.main()
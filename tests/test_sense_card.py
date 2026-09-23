# -*- coding: utf-8 -*-
"""B4 H3 · 词义项子卡 senses[]（D-5 §4a）

覆盖：
  - senses[] 子键默认空 list、to_dict 透出
  - 白名单词义项子卡读写 round-trip：review_feedback 带 sense_id → 子卡独立
    FSRS(s_* 四字段)更新，词级四字段同步更新（词形整体记忆）
  - 非白名单（senses=[]）且有 sense_id → 零行为变化（sense=None、词级单卡照常）
  - senses[] 空 list 向后兼容（store.load 旧图谱 JSON 载入零迁移）
运行: python -m unittest tests.test_sense_card -v
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.graph.error_graph import ErrorGraph
from engine.graph.model import Node
from engine.scheduler.fsrs_adapter import FsrsSchedulerAdapter

_ADAPTER = FsrsSchedulerAdapter()


def _ts(days_ago: float) -> str:
    t = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _sense_card(sid="打-01", stability=0.0, difficulty=5.0):
    return {"sense_id": sid, "gloss": "", "example": "",
            "s_stability": stability, "s_difficulty": difficulty,
            "s_last_review_at": None, "s_next_review_at": None}


class SensesModelTest(unittest.TestCase):

    def test_default_empty_list(self):
        n = Node(id="K", knowledge_point="K", level="HSK1")
        self.assertEqual(n.senses, [])
        self.assertIn("senses", n.to_dict())
        self.assertEqual(n.to_dict()["senses"], [])

    def test_to_dict_transmits_senses(self):
        n = Node(id="打", knowledge_point="打", level="HSK1",
                 senses=[_sense_card()])
        self.assertEqual(n.to_dict()["senses"][0]["sense_id"], "打-01")


class SenseRoutingTest(unittest.TestCase):

    def test_whitelist_word_sense_roundtrip(self):
        g = ErrorGraph("sense1")
        g._nodes = {"打": Node(id="打", knowledge_point="打", level="HSK1",
                               senses=[_sense_card("打-01"), _sense_card("打-02")])}
        out = g.review_feedback("打", rating=3, event_key="e1", sense_id="打-01")
        self.assertEqual(out["status"], "review_feedback")
        self.assertIsNotNone(out["sense"])
        self.assertEqual(out["sense"]["sense_id"], "打-01")
        self.assertGreaterEqual(out["sense"]["interval_days"], 1)
        # 子卡独立 FSRS 已写
        nd = out["node"]
        s1 = next(s for s in nd["senses"] if s["sense_id"] == "打-01")
        self.assertGreater(s1["s_stability"], 0)
        self.assertIsNotNone(s1["s_last_review_at"])
        self.assertIsNotNone(s1["s_next_review_at"])
        # 词级四字段同步更新（词形整体记忆）
        self.assertGreater(nd["fsrs_stability"], 0)
        self.assertIsNotNone(nd["next_review_at"])
        # 未路由的兄弟义项不受影响（子卡独立）
        s2 = next(s for s in nd["senses"] if s["sense_id"] == "打-02")
        self.assertEqual(s2["s_stability"], 0)
        self.assertIsNone(s2["s_last_review_at"])

    def test_sense_id_unmatched_keeps_word_path(self):
        g = ErrorGraph("sense2")
        g._nodes = {"打": Node(id="打", knowledge_point="打", level="HSK1",
                               senses=[_sense_card("打-01")])}
        out = g.review_feedback("打", rating=3, event_key="e2", sense_id="打-99")
        self.assertIsNone(out["sense"])          # 未命中 → 不路由子卡
        self.assertGreater(out["node"]["fsrs_stability"], 0)  # 词级单卡照常

    def test_non_whitelist_zero_behavior_change(self):
        # senses=[]（非白名单词）→ sense_id 传入也走词级单卡，sense=None
        g = ErrorGraph("sense3")
        g._nodes = {"K": Node(id="K", knowledge_point="K", level="HSK1")}
        out = g.review_feedback("K", rating=3, event_key="e3", sense_id="K-01")
        self.assertIsNone(out["sense"])
        self.assertGreater(out["node"]["fsrs_stability"], 0)
        self.assertEqual(out["node"]["senses"], [])

    def test_sense_subcard_cold_start_uses_init_state(self):
        g = ErrorGraph("sense4")
        g._nodes = {"打": Node(id="打", knowledge_point="打", level="HSK1",
                               senses=[_sense_card("打-01")],
                               created_at=_ts(days_ago=10))}
        out = g.review_feedback("打", rating=3, event_key="e4", sense_id="打-01")
        # 子卡冷启动 → init_state —— 期望由内核推出（关系断言，规避 FSRS 漂移）
        expect = round(_ADAPTER.interval(_ADAPTER.init_state(3).stability))
        self.assertEqual(out["sense"]["interval_days"], max(1, expect))


class SensesBackwardCompatTest(unittest.TestCase):

    def test_store_load_missing_senses_defaults_empty(self):
        # 旧图谱 JSON 无 senses 键 → 零迁移载入为 []
        from engine.graph.store import GraphStore
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "graph_old.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"learner_id": "old",
                           "nodes": {"K": {"id": "K", "knowledge_point": "K",
                                           "level": "HSK1"}},
                           "edges": {}, "queue": {}}, f)
            store = GraphStore()
            store.load("old", path=path)
            self.assertEqual(store.nodes["K"].senses, [])

    def test_store_load_preserves_senses(self):
        from engine.graph.store import GraphStore
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "graph_new.json")
            data = {"learner_id": "new",
                    "nodes": {"打": {"id": "打", "knowledge_point": "打",
                                     "level": "HSK1",
                                     "senses": [_sense_card("打-01")]}},
                    "edges": {}, "queue": {}}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            store = GraphStore()
            store.load("new", path=path)
            self.assertEqual(store.nodes["打"].senses[0]["sense_id"], "打-01")


if __name__ == "__main__":
    unittest.main()
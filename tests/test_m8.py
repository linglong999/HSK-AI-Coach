# ============================================================
# M8 记忆 × 图谱打通 · 回归测试（tests/test_m8.py）
# 覆盖：error_ledger / writeback / summarize（决策 A-乙/B-乙/C-乙）
# ============================================================

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.graph.error_graph import ErrorGraph
from engine.memory.error_ledger import ErrorLedger
from engine.memory.writeback import Writeback
from engine.memory.summarize import build_profile_summary, build_profile_facts


class TestErrorLedger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.ledger = ErrorLedger(learner_id="m8", root=self._tmp)

    def test_record_basic(self):
        eid = self.ledger.record("observation_error", "kp-ba-sentence", signature="把")
        self.assertTrue(eid)
        self.assertEqual(self.ledger.count_for_kp("kp-ba-sentence"), 1)

    def test_repeated_error_on_same_signature(self):
        # 同 kp + 同 signature 再现 → repeated_error
        self.ledger.record("observation_error", "kp-ba-sentence", signature="把")
        self.ledger.record("observation_error", "kp-ba-sentence", signature="把")
        self.ledger.record("observation_error", "kp-ba-sentence", signature="把")
        events = self.ledger.recent()
        kinds = [e["kind"] for e in events]
        self.assertIn("repeated_error", kinds)

    def test_repeat_offender_threshold(self):
        # 决策 B-乙：>=3 才算惯犯；2 次不算
        for _ in range(2):
            self.ledger.record("observation_error", "kp-ba", signature="把")
        self.assertFalse(self.ledger.is_repeat_offender("kp-ba", threshold=3))
        self.ledger.record("observation_error", "kp-ba", signature="把")
        self.assertTrue(self.ledger.is_repeat_offender("kp-ba", threshold=3))

    def test_distinct_signature_limit(self):
        # 同 kp 不同 signature 计入 repeated_count，但各计 1
        self.ledger.record("observation_error", "kp-a", signature="s1")
        self.ledger.record("observation_error", "kp-a", signature="s2")
        self.assertEqual(self.ledger.repeated_count("kp-a"), 2)

    def test_invalid_kind(self):
        with self.assertRaises(ValueError):
            self.ledger.record("bad_kind", "kp")

    def test_confirm_does_not_inflate_repeat_count(self):
        # 确认/复习事件不计入撞错次数：错 2 次 + 确认 ≠ 惯犯（宁漏勿错）
        for _ in range(2):
            self.ledger.record("observation_error", "kp-ba", signature="把")
        self.ledger.confirm("kp-ba")
        self.ledger.review("kp-ba")
        self.assertEqual(self.ledger.repeated_count("kp-ba"), 2)
        self.assertFalse(self.ledger.is_repeat_offender("kp-ba"))

    def test_second_confirm_stays_concept_confirmed(self):
        # 同 KP 二次确认仍记 concept_confirmed，不误标 repeated_error
        # （跨轮兜底确认 + 同轮确认可叠加）
        self.ledger.confirm("kp-ba")
        self.ledger.confirm("kp-ba")
        kinds = [e["kind"] for e in self.ledger.recent() if e["kp_id"] == "kp-ba"]
        self.assertEqual(kinds, ["concept_confirmed", "concept_confirmed"])

    def test_reload_persistence(self):
        self.ledger.record("concept_confirmed", "kp-ba", signature="kp-ba")
        self.ledger.reload()
        self.assertEqual(self.ledger.count_for_kp("kp-ba"), 1)


class TestWriteback(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.graph = ErrorGraph(learner_id="m8")
        self.wb = Writeback(graph=self.graph, learner_id="m8", root=self._tmp)
        # Writeback 默认构造 ledger 时已用该 root
        self.wb.ledger = ErrorLedger(learner_id="m8", root=self._tmp)

    def test_suspected_writes_pending(self):
        # 疑似 + kp 命中但 uncertain → pending 队列（复用引擎两段式）
        res = self.wb.on_suspected({
            "fragment": "把", "type": "语法", "type_confident": True,
            "confidence": 0.6, "knowledge_point_id": "kp-ba-sentence",
            "uncertain": True,
        }, event_key="e1")
        self.assertEqual(res["status"], "to_queue")

    def test_confirmed_records_ledger(self):
        eid = self.wb.on_confirmed("kp-ba-sentence", evidence="复核通过")
        self.assertTrue(eid)
        self.assertEqual(self.wb.ledger.count_for_kp("kp-ba-sentence"), 1)

    def test_offender_through_writeback(self):
        for _ in range(3):
            self.wb.ledger.record("observation_error", "kp-ba", signature="把")
        self.assertTrue(self.wb.is_repeat_offender("kp-ba"))

    def test_no_graph_suspected_ledger_only(self):
        wb2 = Writeback(graph=None, learner_id="m8", root=self._tmp)
        res = wb2.on_suspected({"fragment": "把"})
        self.assertEqual(res["status"], "ledger_only")

    def test_review_queue_empty_bytes_graph_none(self):
        wb2 = Writeback(graph=None, learner_id="m8", root=self._tmp)
        self.assertEqual(wb2.review_queue(), [])


class TestSummarize(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.graph = ErrorGraph(learner_id="m8")
        self.ledger = ErrorLedger(learner_id="m8", root=self._tmp)
        # 建一个高优先级 KP：error_count 高、mastery 低
        self.graph.ingest_error({
            "fragment": "把", "type": "语法", "type_confident": True,
            "confidence": 0.95, "knowledge_point_id": "kp-ba-sentence",
            "knowledge_point_name": "把字句", "level": "HSK3",
        }, event_key="e1")
        # 复述通过（mastery 升，但 error_count 高仍排队）
        self.graph.ingest_verdict({
            "fragment": "把", "type": "语法",
            "knowledge_point_id": "kp-ba-sentence",
            "knowledge_point_name": "把字句", "level": "HSK3",
        }, verdict="pass", uncertain=False, event_key="e2")

    def test_summary_contains_kp(self):
        out = build_profile_summary(self.graph, self.ledger)
        self.assertIn("把字句", out)
        self.assertIn("常错点", out)

    def test_focus_kp_only(self):
        # 决策 C-乙：focus_kp 只含该 KP
        out = build_profile_summary(self.graph, self.ledger, focus_kp="kp-other")
        self.assertNotIn("把字句", out)

    def test_offender_tag(self):
        for _ in range(3):
            self.ledger.record("observation_error", "kp-ba-sentence", signature="把")
        out = build_profile_summary(self.graph, self.ledger)
        self.assertIn("惯犯", out)

    def test_empty_graph_safe(self):
        g2 = ErrorGraph(learner_id="m8x")
        out = build_profile_summary(g2, self.ledger)
        self.assertEqual(out, "")

    def test_none_graph_safe(self):
        self.assertEqual(build_profile_summary(None, self.ledger), "")

    def test_facts_latest_evidence_keeps_error_sentence(self):
        # 确认后 latest_evidence 仍是最近一次错误原句，不被"复述验证通过"覆盖；
        # repeated 只数撞错事件（确认不计入）
        self.ledger.record("observation_error", "kp-ba-sentence",
                           signature="把", evidence="我把苹果买了")
        self.ledger.confirm("kp-ba-sentence", evidence="复述验证通过")
        facts = build_profile_facts(self.graph, self.ledger)
        f0 = next(f for f in facts if f["kp_id"] == "kp-ba-sentence")
        self.assertEqual(f0["latest_evidence"], "我把苹果买了")
        self.assertEqual(f0["repeated"], 1)


if __name__ == "__main__":
    unittest.main()
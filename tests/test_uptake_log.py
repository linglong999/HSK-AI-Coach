# -*- coding: utf-8 -*-
"""B2 F5 账本扩展测试：feedback_presented / uptake_observed 两 kind 记账 +
KINDS 校验 + 不会被误改 repeated_error。

对应实施计划-v3 B2 F5：两新 kind 走 record() 既有幂等链 + KINDS 扩展；
复现与确认侧（repeated_error / concept_confirmed）链路无回归。"""
import unittest
import uuid

from engine.memory.error_ledger import ErrorLedger, KINDS


class LedgerKindsExtensionTest(unittest.TestCase):
    def setUp(self):
        # 每测试独立 learner_id：共享同 SQLite 文件（同一 root），避免跨方法互污染
        self.ledger = ErrorLedger(learner_id=f"uptake-{uuid.uuid4().hex[:8]}",
                                  root="data/test_uptake")
        self.ledger.reload()

    def test_two_kinds_in_valid_set(self):
        self.assertIn("feedback_presented", KINDS)
        self.assertIn("uptake_observed", KINDS)

    def test_present_feedback_records_kind(self):
        eid = self.ledger.present_feedback("kp-ba", signature="把书在桌子上放了")
        events = self.ledger.recent()
        self.assertTrue(any(e["kind"] == "feedback_presented"
                            and e["kp_id"] == "kp-ba" for e in events))

    def test_present_feedback_with_fragment_signature(self):
        self.ledger.present_feedback("kp-ba", signature="把这件事知道")
        self.ledger.present_feedback("kp-ba", signature="把这件事知道")
        # feedback_presented 同 kp+signature 再现 ≠ observation_error，
        # 不应被误改 repeated_error
        kinds = [e["kind"] for e in self.ledger.recent()]
        self.assertNotIn("repeated_error", kinds)
        self.assertEqual(kinds.count("feedback_presented"), 2)

    def test_feedback_presented_not_affect_observation_repeat(self):
        # observation_error 的同签名投诉逻辑不受新 kind 干扰
        self.ledger.record("observation_error", "kp-ba", signature="事件A")
        self.ledger.present_feedback("kp-ba", signature="事件A")
        self.ledger.record("observation_error", "kp-ba", signature="事件A")
        kinds = [e["kind"] for e in self.ledger.recent()]
        # 第二个 observation_error 同签名 → repeated_error；feedback_presented 保持原 kind
        self.assertIn("repeated_error", kinds)
        self.assertIn("feedback_presented", kinds)

    def test_observe_uptake(self):
        self.ledger.observe_uptake("kp-ba", evidence="verify pass")
        events = self.ledger.recent()
        self.assertTrue(any(e["kind"] == "uptake_observed"
                            and e["kp_id"] == "kp-ba" for e in events))

    def test_invalid_kind_still_rejected(self):
        with self.assertRaises(ValueError):
            self.ledger.record("not_a_kind", "kp-x")

    def test_repair_semantics_mapped_manually(self):
        # repair=partial 映射是调用方逻辑，这里仅确认 ledger 不丢 evidence
        eid = self.ledger.record("concept_confirmed", "kp-ba",
                                 signature="kp-ba", evidence="repair=partial")
        ev = [e for e in self.ledger.recent() if e["id"] == eid][0]
        self.assertIn("repair=partial", ev["evidence"])


if __name__ == "__main__":
    unittest.main()
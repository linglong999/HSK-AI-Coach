import os
import tempfile
import unittest
from unittest.mock import patch

from engine.dialog_run import DialogRun
from engine.graph.error_graph import ErrorGraph
from engine.memory.error_ledger import ErrorLedger
from engine.memory.writeback import Writeback
from tests.test_dialog_run_smoke import DialogSmokeBase, REPLY, USER_INPUT, KP_ID


class RetellTargetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hsk_retell_target_")
        self.graph = ErrorGraph("retell-test")
        self.graph_path = os.path.join(self.tmp, "graph.json")
        save = self.graph.save
        self.graph.save = lambda path=None: save(path or self.graph_path)
        self.ledger = ErrorLedger("retell-test", root=self.tmp)
        self.wb = Writeback(graph=self.graph, ledger=self.ledger,
                            learner_id="retell-test", root=self.tmp)
        self.router = type("Router", (), {"graph": self.graph})()

    def _run(self, trace, state=None, pre_scan=None, strategies=None):
        obj = DialogRun(router=self.router, planner=None, identify_skill=None,
                        mem=None, wb=self.wb, tracker=None,
                        normalize_user_level=lambda value: value,
                        writeback_ledger_events=lambda *a, **k: [],
                        assistant_payload=lambda *a, **k: {"why": []},
                        retell_state=state or {"current": None})
        out = obj._apply_retell_outcomes(
            trace, {"pre_scan": pre_scan or {}, "strategies": strategies or {}},
            "conv-retell")
        return obj, out

    def _seed(self, kp="kp-x"):
        self.graph.ingest_error({"knowledge_point_id": kp,
                                 "fragment": "错误片段", "type": "语法"},
                                event_key=f"seed:{kp}")

    def _target(self, kp="kp-x"):
        return {"current": {"kp_id": kp, "fragment": "错误片段",
                             "type": "语法", "uncertain": False,
                             "set_in_round": False}}

    def _verify(self, verdict="pass", text="复述"):
        return {"name": "verify_retell", "ok": True,
                "params": {"restatement": text},
                "result": {"verdict": verdict, "covered_points": 1,
                            "total_points": 1, "degraded": False}}

    def _confirmed(self):
        return [e for e in self.ledger.recent() if e["kind"] == "concept_confirmed"]

    def test_cross_round_pass_real_graph_and_ledger(self):
        self._seed(); before = self.graph._nodes["kp-x"].to_dict()
        _, out = self._run([self._verify()], self._target())
        node = self.graph._nodes["kp-x"]
        self.assertGreater(node.mastery, before["mastery"])
        self.assertEqual(node.unfixed_streak, 0); self.assertTrue(node.last_learnt_at)
        self.assertEqual(len(self._confirmed()), 1)
        self.assertEqual(out[0]["target_source"], "previous_round")

    def test_same_round_explain_then_pass_real_graph(self):
        self._seed()
        trace = [{"name": "explain_error", "ok": True,
                  "params": {"error": {"knowledge_point_id": "kp-x"}}},
                 self._verify()]
        _, out = self._run(trace, pre_scan={"errors": [{"knowledge_point_id": "kp-x"}]})
        self.assertEqual(out[0]["target_source"], "same_round")
        self.assertTrue(self.graph._nodes["kp-x"].last_learnt_at)
        self.assertEqual(len(self._confirmed()), 1)

    def test_fail_changes_graph_consumes_target_and_does_not_confirm(self):
        self._seed(); node = self.graph._nodes["kp-x"]; node.mastery = 0.8; before = node.mastery
        obj, out = self._run([self._verify("fail")], self._target())
        self.assertLess(node.mastery, before); self.assertEqual(node.unfixed_streak, 1)
        self.assertFalse(self._confirmed()); self.assertIsNone(obj._retell_state["current"])
        self.assertEqual(out[0]["target_source"], "previous_round")

    def test_partial_preserves_real_graph_ledger_and_target(self):
        self._seed(); before = self.graph._nodes["kp-x"].to_dict()
        obj, out = self._run([self._verify("partial")], self._target())
        self.assertEqual(self.graph._nodes["kp-x"].to_dict(), before)
        self.assertFalse(self._confirmed()); self.assertEqual(obj._retell_state["current"]["kp_id"], "kp-x")
        self.assertEqual(out[0]["target_source"], "previous_round")

    def test_no_target_does_nothing(self):
        self._seed(); before = self.graph._nodes["kp-x"].to_dict()
        _, out = self._run([self._verify()])
        self.assertEqual(self.graph._nodes["kp-x"].to_dict(), before)
        self.assertFalse(self._confirmed()); self.assertEqual(out[0]["kp_id"], "")
        self.assertEqual(out[0]["target_source"], "none"); self.assertFalse(out[0]["graph_applied"])

    def test_fabricated_kp_is_ignored(self):
        self._seed()
        trace = [{"name": "explain_error", "ok": True,
                  "params": {"error": {"knowledge_point_id": "fake"}}}, self._verify()]
        _, out = self._run(trace, self._target(), {"errors": [{"knowledge_point_id": "kp-x"}]})
        self.assertEqual(out[0]["kp_id"], "kp-x"); self.assertEqual(len(self._confirmed()), 1)
        self.assertEqual(self.graph._nodes["kp-x"].unfixed_streak, 0)

    def test_only_target_is_confirmed_among_three_identified(self):
        for kp in ("kp-x", "kp-y", "kp-z"): self._seed(kp)
        _, out = self._run([self._verify()], self._target(),
                           {"errors": [{"knowledge_point_id": kp} for kp in ("kp-x", "kp-y", "kp-z")]})
        self.assertEqual([e["kp_id"] for e in self._confirmed()], ["kp-x"])
        self.assertEqual(out[0]["kp_id"], "kp-x")
        self.assertEqual(self.graph._nodes["kp-y"].unfixed_streak, 0)
        self.assertEqual(self.graph._nodes["kp-z"].unfixed_streak, 0)

    def test_unique_light_mark_becomes_next_round_target(self):
        self._seed(); obj, _ = self._run([], strategies={"light_marks": [{"knowledge_point_id": "kp-x"}]})
        _, out = self._run([self._verify()], obj._retell_state)
        self.assertEqual(out[0]["kp_id"], "kp-x"); self.assertEqual(out[0]["target_source"], "previous_round")
        self.assertTrue(self.graph._nodes["kp-x"].last_learnt_at)

    def test_multiple_light_marks_clear_target(self):
        self._seed(); obj, _ = self._run([], self._target(), strategies={"light_marks": [{"knowledge_point_id": "kp-y"}, {"knowledge_point_id": "kp-z"}]})
        _, out = self._run([self._verify()], obj._retell_state)
        self.assertEqual(out[0]["kp_id"], ""); self.assertFalse(self._confirmed())
        self.assertEqual(self.graph._nodes["kp-x"].mastery, 0.0)

    def test_idempotent_replay_changes_mastery_once_and_reports_false(self):
        self._seed(); obj, first = self._run([self._verify(text="same")], self._target())
        mastery = self.graph._nodes["kp-x"].mastery
        _, second = self._run([self._verify(text="same")], self._target())
        self.assertEqual(self.graph._nodes["kp-x"].mastery, mastery)
        self.assertTrue(first[0]["graph_applied"]); self.assertFalse(second[0]["graph_applied"])
        self.assertEqual(self.graph._nodes["kp-x"].unfixed_streak, 0)

    def test_conversation_targets_are_isolated(self):
        self._seed(); obj, _ = self._run([], self._target())
        _, out = self._run([self._verify()], {"current": None})
        self.assertEqual(out[0]["kp_id"], ""); self.assertEqual(obj._retell_state["current"]["kp_id"], "kp-x")

    def test_a1_event_payload_has_target_fields(self):
        self._seed(); events = []
        obj, out = self._run([self._verify()], self._target())
        obj._study_event = lambda et, payload, key: events.append(payload)
        obj._emit_study_learning_result({"retell_outcomes": out}, "conv-retell")
        self.assertEqual(events[0]["kp_id"], "kp-x")
        self.assertEqual(events[0]["target_source"], "previous_round")
        self.assertTrue(events[0]["graph_applied"])

    def test_graph_write_failure_is_nonfatal(self):
        self._seed(); obj = DialogRun(router=self.router, planner=None, identify_skill=None,
            mem=None, wb=self.wb, tracker=None, normalize_user_level=lambda v: v,
            writeback_ledger_events=lambda *a, **k: [], assistant_payload=lambda *a, **k: {"why": []},
            retell_state=self._target())
        with patch.object(self.graph, "ingest_verdict", side_effect=RuntimeError("x")):
            out = obj._apply_retell_outcomes([self._verify()], {}, "conv-retell")
        self.assertFalse(out[0]["graph_applied"])
        self.assertEqual(obj._retell_notices[0]["reason"], "retell_writeback_failed")


class RetellWritebackDialogTest(DialogSmokeBase):
    def test_graph_write_failure_dialog_returns_200_with_notice(self):
        result = {"text": REPLY, "steps": 2, "fallback": False,
                  "used_skills": ["verify_retell"], "trace": [{
                      "name": "verify_retell", "ok": True,
                      "params": {"restatement": "复述"}, "result": {"verdict": "pass"}}]}
        planner = self.svc._get_planner(None)
        first = {"text": REPLY, "steps": 1, "fallback": False,
                 "used_skills": [], "trace": []}
        with patch.object(DialogRun, "_select_and_log_strategies", return_value={"light_marks": [{"knowledge_point_id": KP_ID}]}), \
             patch.object(planner, "run", return_value=first):
            self.assertEqual(self.svc.dialog({"text": USER_INPUT})[0], 200)
        with patch.object(planner, "run", return_value=result), \
             patch.object(self.router.graph, "ingest_verdict", side_effect=RuntimeError("x"), create=True):
            status, out = self.svc.dialog({"text": USER_INPUT})
        self.assertEqual(status, 200)
        self.assertTrue(any(n["reason"] == "retell_writeback_failed" for n in out["degraded"]))

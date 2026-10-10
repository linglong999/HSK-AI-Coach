"""A1 主链闭环：使用假 planner / 图谱，SQLite 只写临时目录。"""

import json
from unittest.mock import patch
from types import SimpleNamespace

from engine.dialog_run import DialogRun
from tests.test_dialog_run_smoke import DialogSmokeBase, REPLY, USER_INPUT


class StudyFlowTest(DialogSmokeBase):
    def setUp(self):
        super().setUp()
        self.router.learner_id = "a1-smoke"
        self.svc.get_study_tracker().create_session(learner_id="a1-smoke")
        self.router.graph.get_kp = lambda kp: {"node": {"mastery": 0.9}}

    def _consented_session(self):
        tracker = self.svc.get_study_tracker()
        study_id = tracker.create_session(scene_type="language_difficulty", learner_id="a1-smoke")
        tracker.consent(study_id, "consent-v1")
        return tracker, study_id

    def _planner_result(self, verdict="pass"):
        return {"text": REPLY, "steps": 2, "fallback": False,
                "used_skills": ["verify_retell"], "trace": [{
                    "name": "explain_error", "ok": True,
                    "params": {"error": {"knowledge_point_id": "kp-study",
                                           "fragment": "片段", "type": "语法"}},
                    "result": {},
                }, {
                    "name": "verify_retell", "ok": True,
                    "result": {"verdict": verdict, "covered_points": 2,
                               "total_points": 3, "degraded": False},
                }]}

    def _events(self, tracker, study_id):
        rows = tracker._conn.execute(
            "SELECT event_type, event_key, payload_json FROM study_events "
            "WHERE study_session_id=? ORDER BY id", (study_id,),
        ).fetchall()
        return [(row["event_type"], row["event_key"],
                 json.loads(row["payload_json"])) for row in rows]

    def _dialog(self, study_id=None, cid="flow-c1"):
        payload = {"text": USER_INPUT, "conversation_id": cid}
        if study_id:
            payload["study_session_id"] = study_id
        return self.svc.dialog(payload)

    def test_closed_loop_strict_order_coverage_queue_and_no_raw_text(self):
        tracker, study_id = self._consented_session()
        queue = [{"kp_id": "review-1"}, {"kp_id": "review-2"}]
        planner = self.svc._get_planner(None)
        with patch.object(planner, "run", return_value=self._planner_result()), \
                patch.object(self.router.graph, "get_review_queue", return_value=queue):
            status, _ = self._dialog(study_id)
        self.assertEqual(status, 200)
        events = self._events(tracker, study_id)
        self.assertEqual([event[0] for event in events], [
            "input_observed", "intervention_decided", "writeback_completed",
            "learning_confirmed", "review_queue_observed",
            "feedback_presented", "dialog_completed",
        ])
        confirmed = events[3][2]
        self.assertEqual(confirmed["verdict"], "pass")
        self.assertEqual(confirmed["covered_points"], 2)
        self.assertEqual(confirmed["total_points"], 3)
        self.assertFalse(confirmed["degraded"])
        self.assertEqual(confirmed["source"], "verify_retell")
        self.assertNotIn("kp_ids", confirmed)
        self.assertTrue(events[3][1].endswith(":learning_result:1"))
        self.assertEqual(events[2][2]["retell_pass_count"], 1)
        self.assertEqual(events[4][2]["queue_count"], len(queue))
        raw_rows = tracker._conn.execute(
            "SELECT payload_json FROM study_events WHERE study_session_id=?",
            (study_id,),
        ).fetchall()
        self.assertEqual(len(raw_rows), 7)
        for row in raw_rows:
            self.assertNotIn(USER_INPUT, row[0])
            self.assertNotIn("text_sha256", row[0])

    def test_structured_feedback_a1_matches_decisions_and_context_signals(self):
        tracker = self.svc.get_study_tracker()
        planner = self.svc._get_planner(None)

        def run_case(text, sid, cid, *, scene_id="", tracker_result=None):
            captured = []
            original = DialogRun._select_and_log_strategies

            def wrapped(run, *args, **kwargs):
                result = original(run, *args, **kwargs)
                captured.append(result)
                return result

            with patch.object(planner, "run", return_value=self._planner_result()), \
                    patch.object(DialogRun, "_select_and_log_strategies", new=wrapped):
                if tracker_result is not None:
                    run_tracker = self.svc._get_tracker(cid)
                    with patch.object(run_tracker, "observe", return_value=tracker_result):
                        status, _ = self.svc.dialog({"text": text, "conversation_id": cid,
                                                     "study_session_id": sid, "scene_id": scene_id})
                else:
                    status, _ = self.svc.dialog({"text": text, "conversation_id": cid,
                                                 "study_session_id": sid, "scene_id": scene_id})
            self.assertEqual(status, 200)
            self.assertTrue(captured and captured[-1].get("decisions"))
            events = self._events(tracker, sid)
            feedback = [payload for kind, _, payload in events if kind == "feedback_presented"][-1]
            self.assertEqual(feedback["strategies"], [
                {"kp_id": d["target_kp"][:120], "mode": d["mode"],
                 "hint_level": d["hint_level"], "reason_code": d["reason_code"]}
                for d in captured[-1]["decisions"] if d["target_kp"]
            ])
            return captured[-1]["decisions"]

        scene_sid = tracker.create_session(learner_id="a1-smoke")
        tracker.consent(scene_sid, "consent-v1")
        scene_decisions = run_case(USER_INPUT, scene_sid, "scene-c1", scene_id="market")
        self.assertEqual(scene_decisions[0]["reason_code"], "SCENE_LOW_IMPACT")

        help_sid = tracker.create_session(learner_id="a1-smoke")
        tracker.consent(help_sid, "consent-v1")
        with patch("engine.intervention.detect_help_intent", return_value=False):
            help_decisions = run_case("请帮我解释这个错误", help_sid, "help-c1",
                                      tracker_result=("block", "help"))
        self.assertEqual(help_decisions[0]["reason_code"], "HELP_REQUESTED")

        mastery_sid = tracker.create_session(learner_id="a1-smoke")
        tracker.consent(mastery_sid, "consent-v1")
        mastery_scan = dict(self.router.recognizer.recognize(USER_INPUT))
        mastery_scan["errors"] = [dict(mastery_scan["errors"][0], knowledge_point_id="mastery-kp")]
        with patch.object(self.router.recognizer, "recognize", return_value=mastery_scan):
            mastery_decisions = run_case(USER_INPUT, mastery_sid, "mastery-c1",
                                         tracker_result=("light", "streak"))
        self.assertEqual(mastery_decisions[0]["reason_code"], "SLIP_ON_MASTERED")

    def test_feedback_presented_keeps_strategy_without_kp(self):
        tracker, study_id = self._consented_session()
        planner = self.svc._get_planner(None)
        scan = {"errors": [{"fragment": "拜访", "type": "词汇",
                             "verdict": "error", "confidence": 0.9}],
                "uncertain": [], "degraded": []}
        with patch.object(self.router.recognizer, "recognize", return_value=scan), \
                patch.object(planner, "run", return_value=self._planner_result()):
            status, _ = self._dialog(study_id, cid="f2-no-kp")
        self.assertEqual(status, 200)
        feedback = [payload for kind, _, payload in self._events(tracker, study_id)
                    if kind == "feedback_presented"][-1]
        self.assertEqual(feedback["strategy_count"], 1)
        self.assertEqual(feedback["strategies"][0]["kp_id"], "")

    def _assert_verdict(self, verdict, expected):
        tracker, study_id = self._consented_session()
        planner = self.svc._get_planner(None)
        with patch.object(planner, "run", return_value=self._planner_result(verdict)):
            self.assertEqual(self._dialog(study_id)[0], 200)
        learning = [event for event in self._events(tracker, study_id)
                    if event[0].startswith("learning_")]
        self.assertEqual([event[0] for event in learning], [expected])
        self.assertEqual(learning[0][2]["verdict"], verdict)

    def test_pass_produces_only_confirmed(self):
        self._assert_verdict("pass", "learning_confirmed")

    def test_partial_produces_only_pending(self):
        self._assert_verdict("partial", "learning_pending")

    def test_fail_produces_only_rejected(self):
        self._assert_verdict("fail", "learning_rejected")

    def test_trace_order_and_unknown_verdicts(self):
        tracker, study_id = self._consented_session()
        result = self._planner_result()
        result["trace"] = [
            {"name": "verify_retell", "ok": True, "params": {}, "result": {"verdict": "partial"}},
            {"name": "verify_retell", "ok": True, "params": {}, "result": {"verdict": "reject"}},
            {"name": "verify_retell", "ok": True, "params": {}, "result": {"verdict": "fail", "degraded": True}},
            {"name": "verify_retell", "ok": True, "params": {}, "result": {}},
            {"name": "verify_retell", "ok": True, "params": {}, "result": {"verdict": "pass"}},
        ]
        with patch.object(self.svc._get_planner(None), "run", return_value=result):
            self.assertEqual(self._dialog(study_id)[0], 200)
        learning = [event for event in self._events(tracker, study_id)
                    if event[0].startswith("learning_")]
        self.assertEqual([event[0] for event in learning], [
            "learning_pending", "learning_rejected", "learning_confirmed",
        ])
        for event, index in zip(learning, [0, 2, 4]):
            self.assertTrue(event[1].endswith(f":learning_result:{index}"))
            self.assertEqual(event[2]["covered_points"], 0)
            self.assertEqual(event[2]["total_points"], 0)
        self.assertTrue(learning[1][2]["degraded"])

    def test_unconsented_session_returns_400_and_records_nothing(self):
        tracker = self.svc.get_study_tracker()
        study_id = tracker.create_session(learner_id="a1-smoke")
        status, out = self._dialog(study_id)
        self.assertEqual(status, 400)
        self.assertEqual(out["code"], "study_not_recordable")
        self.assertEqual(self._events(tracker, study_id), [])
        self.assertEqual(self.calls, [])

    def test_no_study_reference_is_lazy_and_patch_positive_control(self):
        planner = self.svc._get_planner(None)
        with patch.object(planner, "run", return_value=self._planner_result()), \
                patch("engine.study.reference", side_effect=RuntimeError("reference disabled")) as ref:
            self.assertEqual(self._dialog()[0], 200)
            ref.assert_not_called()
            tracker, study_id = self._consented_session()
            self.assertEqual(self._dialog(study_id, cid="control-c1")[0], 200)
            self.assertEqual(ref.call_count, 7)
            self.assertEqual(self._events(tracker, study_id), [])

    def _assert_stage_failure(self, stage, target, name):
        tracker, study_id = self._consented_session()
        error = RuntimeError("敏感原文不应进入失败事件")
        with patch.object(target, name, side_effect=error):
            with self.assertRaises(RuntimeError) as ctx:
                self._dialog(study_id)
        self.assertIs(ctx.exception, error)
        events = self._events(tracker, study_id)
        self.assertEqual(events[-1][0], "dialog_failed")
        self.assertTrue(events[-1][1].endswith(":failed"))
        self.assertEqual(events[-1][2]["stage"], stage)
        self.assertEqual(events[-1][2]["reason"], "RuntimeError")
        self.assertNotIn("dialog_completed", [event[0] for event in events])
        self.assertNotIn(str(error), json.dumps(events, ensure_ascii=False))

    def test_planner_failure_records_class_and_reraises_same_object(self):
        self._assert_stage_failure("planner", self.svc._get_planner(None), "run")

    def test_pre_scan_failure_records_stage(self):
        self._assert_stage_failure("pre_scan", DialogRun, "_pre_scan_intervene")

    def test_writeback_failure_records_stage(self):
        self._assert_stage_failure("writeback", DialogRun, "_writeback_round")

    def test_response_failure_records_stage(self):
        self._assert_stage_failure("response", DialogRun, "_build_dialog_response")

    def test_failed_builder_cannot_mask_original_exception(self):
        tracker, study_id = self._consented_session()
        error = RuntimeError("original planner error")
        with patch.object(self.svc._get_planner(None), "run", side_effect=error), \
                patch("engine.study.reference", side_effect=ValueError("builder error")):
            with self.assertRaises(RuntimeError) as ctx:
                self._dialog(study_id)
        self.assertIs(ctx.exception, error)
        self.assertEqual(self._events(tracker, study_id), [])

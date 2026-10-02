"""A1 主链闭环：使用假 planner / 图谱，SQLite 只写临时目录。"""

import json
from unittest.mock import patch

from engine.dialog_run import DialogRun
from tests.test_dialog_run_smoke import DialogSmokeBase, REPLY, USER_INPUT


class StudyFlowTest(DialogSmokeBase):
    def _consented_session(self):
        tracker = self.svc.get_study_tracker()
        study_id = tracker.create_session(scene_type="language_difficulty")
        tracker.consent(study_id, "consent-v1")
        return tracker, study_id

    def _planner_result(self, verdict="pass"):
        return {"text": REPLY, "steps": 2, "fallback": False,
                "used_skills": ["verify_retell"], "trace": [{
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
        self.assertTrue(events[3][1].endswith(":learning_result:0"))
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
            {"name": "verify_retell", "result": {"verdict": "partial"}},
            {"name": "verify_retell", "result": {"verdict": "reject"}},
            {"name": "verify_retell", "result": {"verdict": "fail", "degraded": True}},
            {"name": "verify_retell", "result": {}},
            {"name": "verify_retell", "result": {"verdict": "pass"}},
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
        study_id = tracker.create_session()
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

import json

import pytest

from engine.study import (PROTOCOL_VERSION, StudyError, StudyNotRecordable,
                          StudyTracker, _main)


def _input_payload():
    return {
        "conversation_ref": "conv-ref",
        "text_chars": 6,
        "scene_type": "survival",
    }


def test_study_requires_consent_and_transitions_to_active(tmp_path):
    tracker = StudyTracker(root=str(tmp_path), now=lambda: 1000,
                           id_factory=lambda: "A1-test")
    study_id = tracker.create_session(scene_type="survival")

    with pytest.raises(StudyNotRecordable):
        tracker.record(study_id, "input_observed", _input_payload(), "input-1")

    tracker.consent(study_id, "consent-v1")
    event_id = tracker.record(study_id, "input_observed", _input_payload(), "input-1")
    assert event_id > 0
    assert tracker.status(study_id)["status"] == "active"
    summary = tracker.export_summary(study_id)["sessions"][0]
    assert summary["event_count"] == 1
    assert summary["event_types"] == {"input_observed": 1}


def test_study_event_whitelist_and_idempotency(tmp_path):
    tracker = StudyTracker(root=str(tmp_path), id_factory=lambda: "A1-test")
    study_id = tracker.create_session()
    tracker.consent(study_id, "consent-v1")
    payload = {
        "conversation_ref": "conv-ref",
        "level": "light",
        "reason": "material",
        "error_count": 0,
        "uncertain_count": 0,
        "scanned": False,
    }
    first = tracker.record(study_id, "intervention_decided", payload, "round-1")
    second = tracker.record(study_id, "intervention_decided", payload, "round-1")
    assert first == second

    with pytest.raises(StudyError, match="未允许字段"):
        tracker.record(study_id, "intervention_decided",
                       {**payload, "raw_text": "不要落盘"}, "round-2")
    with pytest.raises(StudyError, match="不允许的试用事件"):
        tracker.record(study_id, "raw_message", {}, "raw-1")


def test_withdraw_clears_events_and_blocks_future_writes(tmp_path):
    tracker = StudyTracker(root=str(tmp_path), id_factory=lambda: "A1-test")
    study_id = tracker.create_session()
    tracker.consent(study_id, "consent-v1")
    tracker.record(study_id, "input_observed", _input_payload(), "input-1")

    result = tracker.withdraw(study_id)
    assert result == {"study_session_id": study_id, "status": "withdrawn",
                      "deleted_events": 1}
    assert tracker.export_summary(study_id)["sessions"][0]["event_count"] == 0
    with pytest.raises(StudyNotRecordable):
        tracker.record(study_id, "input_observed", _input_payload(), "input-2")


def test_delete_removes_session_consent_and_events(tmp_path):
    tracker = StudyTracker(root=str(tmp_path), id_factory=lambda: "A1-test")
    study_id = tracker.create_session()
    tracker.consent(study_id, "consent-v1")
    tracker.record(study_id, "input_observed", _input_payload(), "input-1")

    result = tracker.delete(study_id)
    assert result["deleted"] is True
    assert result["deleted_events"] == 1
    assert tracker.export_summary(study_id)["sessions"] == []
    conn = tracker._conn
    assert conn.execute("SELECT COUNT(*) FROM study_consent").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM study_events").fetchone()[0] == 0


def test_complete_is_idempotent_and_blocks_more_events(tmp_path):
    tracker = StudyTracker(root=str(tmp_path), id_factory=lambda: "A1-test")
    study_id = tracker.create_session()
    tracker.consent(study_id, "consent-v1")
    tracker.record(study_id, "input_observed", _input_payload(), "input-1")
    tracker.complete(study_id, reason="author baseline finished")
    tracker.complete(study_id, reason="same completion")
    assert tracker.status(study_id)["status"] == "completed"
    assert tracker.status(study_id)["completion_reason"] == "author baseline finished"
    with pytest.raises(StudyNotRecordable):
        tracker.record(study_id, "input_observed", _input_payload(), "input-2")


def test_summary_contains_only_aggregates(tmp_path):
    tracker = StudyTracker(root=str(tmp_path), id_factory=lambda: "A1-test")
    study_id = tracker.create_session(protocol_version=PROTOCOL_VERSION)
    tracker.consent(study_id, "consent-v1")
    tracker.record(study_id, "input_observed", _input_payload(), "input-1")
    summary = tracker.export_summary(study_id)
    encoded = json.dumps(summary, ensure_ascii=False)
    assert "text_sha256" not in encoded
    assert "raw_text" not in encoded
    assert "event_count" in encoded


@pytest.mark.parametrize("verdict,event_type", [
    ("pass", "learning_confirmed"),
    ("partial", "learning_pending"),
    ("fail", "learning_rejected"),
])
def test_learning_payload_uses_coverage_not_kp_ids(tmp_path, verdict, event_type):
    tracker = StudyTracker(root=str(tmp_path), id_factory=lambda: "A1-learning")
    study_id = tracker.create_session()
    tracker.consent(study_id, "consent-v1")
    payload = {"conversation_ref": "conv-ref", "verdict": verdict,
               "covered_points": 1, "total_points": 2, "degraded": False,
               "source": "verify_retell"}
    tracker.record(study_id, event_type, payload, "learning-1")
    with pytest.raises(StudyError, match="未允许字段"):
        tracker.record(study_id, event_type, {**payload, "kp_ids": []}, "learning-2")


def test_learning_payload_rejects_kp_id_over_120_chars(tmp_path):
    tracker = StudyTracker(root=str(tmp_path), id_factory=lambda: "A1-kp-length")
    study_id = tracker.create_session()
    tracker.consent(study_id, "consent-v1")
    payload = {"conversation_ref": "conv-ref", "verdict": "pass",
               "covered_points": 1, "total_points": 1, "degraded": False,
               "source": "verify_retell", "kp_id": "k" * 121,
               "target_source": "same_round", "graph_applied": True}
    with pytest.raises(StudyError):
        tracker.record(study_id, "learning_confirmed", payload, "learning-kp-long")


def test_learning_payload_rejects_invalid_target_source(tmp_path):
    tracker = StudyTracker(root=str(tmp_path), id_factory=lambda: "A1-target-source")
    study_id = tracker.create_session()
    tracker.consent(study_id, "consent-v1")
    payload = {"conversation_ref": "conv-ref", "verdict": "pass",
               "covered_points": 1, "total_points": 1, "degraded": False,
               "source": "verify_retell", "kp_id": "kp-x",
               "target_source": "guessed", "graph_applied": True}
    with pytest.raises(StudyError):
        tracker.record(study_id, "learning_confirmed", payload, "learning-source-invalid")


@pytest.mark.parametrize("event_type,payload", [
    ("input_observed", {**_input_payload(), "text_sha256": "a" * 64}),
    ("learning_confirmed", {"verdict": "rejected"}),
    ("learning_pending", {"covered_points": -1}),
    ("learning_rejected", {"total_points": "2"}),
    ("writeback_completed", {"confirmed_count": 1}),
    ("writeback_completed", {"retell_pass_count": -1}),
    ("writeback_completed", {"retell_pass_count": True}),
    ("learning_confirmed", {"degraded": 0}),
    ("dialog_completed", {"fallback": "false"}),
    ("intervention_decided", {"scanned": 1}),
])
def test_payload_rejects_old_fields_and_invalid_types(tmp_path, event_type, payload):
    tracker = StudyTracker(root=str(tmp_path), id_factory=lambda: "A1-invalid")
    study_id = tracker.create_session()
    tracker.consent(study_id, "consent-v1")
    with pytest.raises(StudyError):
        tracker.record(study_id, event_type, payload, "invalid-1")


def test_list_expired_strict_boundary_sort_fields_and_no_deletion(tmp_path):
    clock = [300]
    ids = iter(["A1-later", "A1-earlier", "A1-boundary", "A1-future"])
    tracker = StudyTracker(root=str(tmp_path), now=lambda: clock[0],
                           id_factory=lambda: next(ids), retention_days=1)
    later = tracker.create_session()
    tracker.consent(later, "consent-v1")
    clock[0] = 100
    earlier = tracker.create_session()
    clock[0] = 500
    tracker.create_session()
    clock[0] = 600
    tracker.create_session()
    clock[0] = 86400 + 500
    expected = [
        {"study_session_id": earlier, "status": "created", "retention_until": 86500},
        {"study_session_id": later, "status": "consented", "retention_until": 86700},
    ]
    assert tracker.list_expired() == expected
    assert tracker.list_expired(now=clock[0]) == expected
    assert tracker.list_expired(now=0) == []
    assert tracker.export_summary()["count"] == 4


def test_cli_expired_outputs_json_with_matching_count(tmp_path, monkeypatch, capsys):
    tracker = StudyTracker(root=str(tmp_path), now=lambda: 100,
                           id_factory=lambda: "A1-expired", retention_days=1)
    study_id = tracker.create_session()
    monkeypatch.setattr("engine.study.time.time", lambda: 86501)
    assert _main(["expired", "--root", str(tmp_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"now": 86501, "count": 1, "sessions": [
        {"study_session_id": study_id, "status": "created", "retention_until": 86500},
    ]}
    assert tracker.status(study_id)["status"] == "created"

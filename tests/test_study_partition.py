"""P1.7：真实 SQLite 分区数据、文件及旧库迁移验收。"""

import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import pytest

from engine.dialog_service import DialogService
from engine.memory import store
from engine.memory.error_ledger import ErrorLedger
from engine.memory.learner_memory import LearnerMemory
from engine.metrics import _file
from engine.serve import _resolve_study_mode, create_app
from engine.study import (StudyTracker, StudyError, STUDY_LEARNER_RE,
                          mark_active, clear_active, is_active, _main)
from tests.test_dialog_run_smoke import FakeRouter


@pytest.fixture
def tracker(tmp_path):
    obj = StudyTracker(root=str(tmp_path))
    yield obj
    obj._conn.close()


def seed(root, learner):
    ledger = ErrorLedger(learner, root=str(root))
    ledger.record("observation_error", "kp-x", evidence="真实原句")
    mem = LearnerMemory(learner, root=str(root))
    mem.append("c1", "user", "真实原句")
    mem.update_profile(user_level="HSK3")
    for name in (f"graph_{learner}.json", f"graph_{learner}.json.tmp",
                 Path(_file(str(root), "feedback", learner)).name):
        (root / name).write_text(json.dumps({"learner": learner, "raw": "真实原句"}), encoding="utf-8")
    ledger._conn.close()
    mem._conn.close()


def snapshot(tracker, learner):
    counts = tracker.learner_data_counts(learner)
    rows = {t: [tuple(r) for r in tracker._conn.execute(
        f"SELECT * FROM {t} WHERE learner_id=?", (learner,))]
        for t in ("ledger_events", "sessions", "messages", "profiles", "study_sessions")}
    hashes = {n: hashlib.sha256((Path(tracker.root) / n).read_bytes()).hexdigest()
              for n in counts["files"]}
    return counts, rows, hashes


def service(root, learner="a1-x", sid="", gate=None):
    router = FakeRouter()
    router.learner_id = learner
    return DialogService(router, memory_root=str(root), gate=gate,
                         study_session_id=sid,
                         dialog_llm=lambda messages: '[{"type":"text","content":"你好"}]')


def test_binding_auto_invalid_and_second_session(tracker):
    sid = tracker.create_session()
    learner = tracker.status(sid)["learner_id"]
    assert STUDY_LEARNER_RE.fullmatch(learner)
    with pytest.raises(StudyError):
        tracker.create_session(learner_id="demo")
    second = tracker.create_session(learner_id=learner)
    assert tracker.status(second)["learner_id"] == learner
    assert all(s["learner_id"] == learner for s in tracker.export_summary()["sessions"])


def test_new_partition_with_existing_real_data_is_rejected(tracker, tmp_path):
    seed(tmp_path, "a1-x")
    before = snapshot(tracker, "a1-x")
    with pytest.raises(StudyError, match="已有非试用数据"):
        tracker.create_session(learner_id="a1-x")
    assert snapshot(tracker, "a1-x") == before


def test_memory_items_partition_counts_purge_and_binding_guard(tracker, tmp_path):
    from engine.memory.long_term import LongTermMemory
    a = LongTermMemory("a1-x", root=str(tmp_path), id_factory=lambda: "mem-a")
    d = LongTermMemory("demo", root=str(tmp_path), id_factory=lambda: "mem-d")
    a.add_candidate("life_event", {"summary": "A"}, {})
    did = d.add_candidate("life_event", {"summary": "D"}, {})
    before_demo = tracker._conn.execute(
        "SELECT * FROM memory_items WHERE learner_id='demo'").fetchall()
    assert tracker.learner_data_counts("a1-x")["memory_items"] == 1
    with pytest.raises(StudyError, match="已有非试用数据"):
        tracker.create_session(learner_id="a1-x")
    tracker._conn.execute("INSERT INTO study_sessions (study_session_id, learner_id, protocol_version, scene_type, status, created_at, retention_until) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          ("purge-memory", "a1-x", "v1", "general", "completed", 1, 2))
    tracker._conn.commit()
    result = tracker.purge_learner("a1-x")
    assert result["deleted"]["memory_items"] == 1
    assert tracker.learner_data_counts("a1-x")["memory_items"] == 0
    after_demo = tracker._conn.execute(
        "SELECT * FROM memory_items WHERE learner_id='demo'").fetchall()
    assert [tuple(row) for row in after_demo] == [tuple(row) for row in before_demo]
    assert d.get(did)["status"] == "candidate"


def test_delete_protects_memory_items_partition(tracker, tmp_path):
    from engine.memory.long_term import LongTermMemory
    sid = tracker.create_session(learner_id="a1-x")
    memory = LongTermMemory("a1-x", root=str(tmp_path), id_factory=lambda: "mem-delete")
    memory.add_candidate("life_event", {"summary": "Keep"}, {})
    before = [tuple(row) for row in tracker._conn.execute(
        "SELECT * FROM memory_items WHERE learner_id='a1-x'").fetchall()]
    with pytest.raises(StudyError, match="仍有数据"):
        tracker.delete(sid)
    after = [tuple(row) for row in tracker._conn.execute(
        "SELECT * FROM memory_items WHERE learner_id='a1-x'").fetchall()]
    assert after == before


def test_real_old_table_migration_adds_column_then_index(tmp_path):
    conn = sqlite3.connect(tmp_path / "coach.db")
    conn.execute("""CREATE TABLE study_sessions (
        study_session_id TEXT PRIMARY KEY, protocol_version TEXT NOT NULL,
        scene_type TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL,
        consented_at INTEGER, started_at INTEGER, completed_at INTEGER,
        completion_reason TEXT NOT NULL DEFAULT '', withdrawn_at INTEGER,
        retention_until INTEGER NOT NULL)""")
    conn.execute("INSERT INTO study_sessions (study_session_id,protocol_version,scene_type,status,created_at,retention_until) VALUES ('old','v1','general','created',1,2)")
    conn.commit()
    conn.close()
    conn = store.connect(str(tmp_path))
    assert "learner_id" in {r[1] for r in conn.execute("PRAGMA table_info(study_sessions)")}
    assert "idx_study_sessions_learner" in {r[1] for r in conn.execute("PRAGMA index_list(study_sessions)")}
    assert conn.execute("SELECT learner_id FROM study_sessions WHERE study_session_id='old'").fetchone()[0] == ""
    conn.close()


@pytest.mark.parametrize("router_learner,payload_learner", [("a1-y", "a1-x"), ("a1-x", "a1-y")])
def test_dialog_binding_mismatch_has_no_data_or_quota_writes(tracker, tmp_path, router_learner, payload_learner):
    sid = tracker.create_session(learner_id="a1-x")
    tracker.consent(sid, "v1")
    svc = service(tmp_path, router_learner)
    before = {l: snapshot(tracker, l) for l in ("a1-x", "a1-y")}
    with patch.object(svc, "_consume_visitor_quota") as quota:
        status, out = svc.dialog({"text": "你好", "study_session_id": sid, "learner_id": payload_learner}, vid="visitor")
        quota.assert_not_called()
    assert status == 400 and out["code"] == "study_learner_mismatch"
    assert {l: snapshot(tracker, l) for l in before} == before
    assert tracker.export_summary(sid)["sessions"][0]["event_count"] == 0


def test_default_study_id_records_and_rejects_override(tracker, tmp_path):
    sid = tracker.create_session(learner_id="a1-x")
    tracker.consent(sid, "v1")
    svc = service(tmp_path, sid=sid)
    assert svc.dialog({"text": "你好"})[0] == 200
    assert tracker.export_summary(sid)["sessions"][0]["event_count"] > 0
    status, out = svc.dialog({"text": "你好", "study_session_id": "other"})
    assert status == 400 and out["code"] == "study_session_mismatch"


def test_purge_real_partition_leaves_demo_tables_and_file_hashes_identical(tracker, tmp_path):
    sid = tracker.create_session(learner_id="a1-x")
    tracker.consent(sid, "v1")
    tracker.record(sid, "input_observed", {"text_chars": 4}, "input")
    seed(tmp_path, "a1-x")
    seed(tmp_path, "demo")
    demo_before = snapshot(tracker, "demo")
    before = tracker.learner_data_counts("a1-x")
    tracker.complete(sid)
    result = tracker.purge_learner("a1-x")
    after = tracker.learner_data_counts("a1-x")
    assert all(after[k] == 0 for k in ("ledger_events", "messages", "sessions", "profiles", "study_sessions"))
    assert after["files"] == []
    assert snapshot(tracker, "demo") == demo_before
    for key in ("ledger_events", "messages", "sessions", "profiles", "study_sessions"):
        assert result["deleted"][key] == before[key]
    assert result["deleted"]["study_events"] == 1
    assert result["not_covered"] == ["backups"]
    assert tracker._conn.execute("SELECT COUNT(*) FROM study_consent").fetchone()[0] == 0


@pytest.mark.parametrize("reason", ["consented", "active", "marker", "demo", "unbound"])
def test_purge_rejections_preserve_all_real_data(tracker, tmp_path, reason):
    learner = "demo" if reason == "demo" else "a1-x"
    if reason not in ("demo", "unbound"):
        sid = tracker.create_session(learner_id=learner)
        tracker.consent(sid, "v1")
        if reason == "marker":
            tracker.complete(sid)
            mark_active(str(tmp_path), learner, sid)
        elif reason == "active":
            tracker.record(sid, "input_observed", {"text_chars": 1}, "i")
    seed(tmp_path, learner)
    before = snapshot(tracker, learner)
    with pytest.raises(StudyError):
        tracker.purge_learner(learner)
    assert snapshot(tracker, learner) == before
    if reason == "marker":
        tracker.purge_learner(learner, force=True)
        assert not is_active(str(tmp_path), learner)
        assert tracker.learner_data_counts(learner)["files"] == []


def test_purge_rejects_non_study_learner_even_when_bound(tracker, tmp_path):
    seed(tmp_path, "demo")
    tracker._conn.execute(
        "INSERT INTO study_sessions "
        "(study_session_id, protocol_version, scene_type, status, created_at, "
        "consented_at, completed_at, retention_until, learner_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("demo-bound", "v1", "general", "completed", 1, 1, 2, 3, "demo"),
    )
    tracker._conn.commit()
    before = snapshot(tracker, "demo")
    with pytest.raises(StudyError, match="learner_id 格式无效"):
        tracker.purge_learner("demo")
    assert snapshot(tracker, "demo") == before
    with pytest.raises(StudyError, match="learner_id 格式无效"):
        tracker.purge_learner("demo", force=True)
    assert snapshot(tracker, "demo") == before


def test_delete_protection_and_purge_removes_binding(tracker, tmp_path):
    sid = tracker.create_session(learner_id="a1-x")
    seed(tmp_path, "a1-x")
    before = snapshot(tracker, "a1-x")
    with pytest.raises(StudyError, match="请先执行 purge"):
        tracker.delete(sid)
    assert snapshot(tracker, "a1-x") == before
    tracker.purge_learner("a1-x")
    assert tracker.delete(sid)["deleted"] is False


def test_activity_marker_lifecycle(tmp_path):
    path = mark_active(str(tmp_path), "a1-x", "study1")
    assert set(json.loads(Path(path).read_text(encoding="utf-8"))) == {"learner_id", "study_session_id", "started_at"}
    assert is_active(str(tmp_path), "a1-x")
    clear_active(str(tmp_path), "a1-x")
    assert not is_active(str(tmp_path), "a1-x")
    clear_active(str(tmp_path), "a1-x")


def test_cli_create_inspect_purge_json_and_rejection(tmp_path, capsys):
    root = str(tmp_path)
    assert _main(["create", "--root", root, "--learner-id", "a1-x"]) == 0
    assert json.loads(capsys.readouterr().out)["learner_id"] == "a1-x"
    assert _main(["inspect", "--root", root, "--learner-id", "a1-x"]) == 0
    assert json.loads(capsys.readouterr().out)["study_sessions"] == 1
    assert _main(["purge", "--root", root, "--learner-id", "demo"]) == 2
    assert capsys.readouterr().err
    assert _main(["purge", "--root", root, "--learner-id", "a1-x"]) == 0
    assert json.loads(capsys.readouterr().out)["deleted"]["study_sessions"] == 1


def test_study_mode_resolution(tracker, tmp_path):
    sid = tracker.create_session(learner_id="a1-x")
    with pytest.raises(ValueError):
        _resolve_study_mode(sid, None, str(tmp_path))
    with pytest.raises(ValueError):
        _resolve_study_mode("missing", None, str(tmp_path))
    tracker.consent(sid, "v1")
    with pytest.raises(ValueError):
        _resolve_study_mode(sid, "a1-y", str(tmp_path))
    assert _resolve_study_mode(sid, None, str(tmp_path)) == "a1-x"


def test_file_delete_failure_is_reported_after_database_commit(tracker, tmp_path):
    tracker.create_session(learner_id="a1-x")
    seed(tmp_path, "a1-x")
    with patch("engine.study.os.remove", side_effect=PermissionError("busy")):
        result = tracker.purge_learner("a1-x")
    assert len(result["file_errors"]) == 3
    assert tracker.learner_data_counts("a1-x")["messages"] == 0


def test_purge_sql_failure_rolls_back_every_table_and_leaves_files(tracker, tmp_path):
    tracker.create_session(learner_id="a1-x")
    seed(tmp_path, "a1-x")
    before = snapshot(tracker, "a1-x")
    tracker._conn.execute("CREATE TRIGGER refuse_purge BEFORE DELETE ON profiles BEGIN SELECT RAISE(ABORT, 'refused'); END")
    tracker._conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        tracker.purge_learner("a1-x")
    assert snapshot(tracker, "a1-x") == before


def test_app_passes_default_study_id_and_create_learner(tracker, tmp_path):
    from fastapi.testclient import TestClient
    router = FakeRouter()
    router.learner_id = "a1-x"
    sid = tracker.create_session(learner_id="a1-x")
    tracker.consent(sid, "v1")
    app = create_app(router, str(tmp_path), memory_root=str(tmp_path),
                     study_session_id=sid,
                     dialog_llm=lambda messages: '[{"type":"text","content":"你好"}]')
    with TestClient(app, base_url="http://127.0.0.1:8612") as client:
        assert client.post("/api/dialog", json={"text": "你好"}).status_code == 200
        created = client.post("/api/study/create", json={"learner_id": "a1-y"})
        assert created.json()["learner_id"] == "a1-y"
        invalid = client.post("/api/study/create", json={"learner_id": "demo"})
        assert invalid.status_code == 400
    assert tracker.export_summary(sid)["sessions"][0]["event_count"] > 0


@pytest.mark.parametrize("run_fails", [False, True])
def test_study_main_marks_active_and_cleans_on_exit(tracker, tmp_path, run_fails):
    from engine import serve
    sid = tracker.create_session(learner_id="a1-x")
    tracker.consent(sid, "v1")
    def run(*args, **kwargs):
        assert is_active(str(tmp_path), "a1-x")
        if run_fails:
            raise RuntimeError("server stopped")
    with patch("sys.argv", ["serve", "--study", sid]), \
            patch.object(serve, "_resolve_study_mode", return_value="a1-x"), \
            patch.object(serve, "Router", return_value=SimpleNamespace()), \
            patch.object(serve, "VisitorGate"), \
            patch.object(serve, "create_app") as app, \
            patch("engine.study.mark_active", side_effect=lambda root, l, s: mark_active(str(tmp_path), l, s)), \
            patch("engine.study.clear_active", side_effect=lambda root, l: clear_active(str(tmp_path), l)), \
            patch("uvicorn.run", side_effect=run):
        if run_fails:
            with pytest.raises(RuntimeError):
                serve.main()
        else:
            serve.main()
        assert app.call_args.kwargs["study_session_id"] == sid
    assert not is_active(str(tmp_path), "a1-x")


def test_old_unbound_session_rejected_and_can_be_deleted(tracker, tmp_path):
    sid = tracker.create_session()
    tracker._conn.execute("UPDATE study_sessions SET learner_id='' WHERE study_session_id=?", (sid,))
    tracker._conn.commit()
    tracker.consent(sid, "v1")
    status, out = service(tmp_path).dialog({"text": "你好", "study_session_id": sid})
    assert status == 400 and out["code"] == "study_learner_mismatch"
    assert tracker.delete(sid)["deleted"] is True

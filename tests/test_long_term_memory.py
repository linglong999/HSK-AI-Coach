import json
import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.memory.long_term import LongTermMemory, LongTermMemoryError


def make(tmp_path, learner="a1-x"):
    clock = iter(range(100, 200))
    return LongTermMemory(learner, root=str(tmp_path), now=lambda: next(clock),
                          id_factory=iter(["mem-1", "mem-2", "mem-3", "mem-4"]).__next__)


def content(summary="Trip to Beijing", **extra):
    return {"summary": summary, **extra}


def source():
    return {"conversation_id": "c1", "extractor": "test", "event_key": "e1"}


def test_state_machine_permissions_and_full_combinations(tmp_path):
    m = make(tmp_path)
    candidate = m.add_candidate("life_event", content(place="Beijing"), source())
    with pytest.raises(LongTermMemoryError): m.confirm(candidate, "system")
    assert m.get(candidate)["status"] == "candidate"
    m.confirm(candidate, "learner")
    m.confirm(candidate, "operator")
    with pytest.raises(LongTermMemoryError): m.reject(candidate, "learner")
    m.forget(candidate, "operator")
    m.forget(candidate, "learner")
    assert m.get(candidate)["status"] == "forgotten"

    rejected = m.add_candidate("unfinished_task", content("Finish report", scene_id="class"), source())
    m.reject(rejected, "learner")
    m.reject(rejected, "operator")
    with pytest.raises(LongTermMemoryError): m.confirm(rejected, "learner")
    m.forget(rejected, "learner")
    with pytest.raises(LongTermMemoryError): m.reject(rejected, "learner")


def test_dedup_rejected_and_forgotten_are_tombstones(tmp_path):
    m = make(tmp_path)
    first = m.add_candidate("life_event", content("  Trip   TO Beijing "), source())
    second = m.add_candidate("life_event", content("trip to beijing"), {"extractor": "other"})
    assert first == second
    assert len(m.list_items()) == 1
    m.reject(first, "learner")
    assert m.add_candidate("life_event", content("trip to beijing"), source()) == first
    assert m.get(first)["status"] == "rejected"
    m.forget(first, "learner")
    assert m.add_candidate("life_event", content("trip to beijing"), source()) == first
    assert m.get(first)["status"] == "forgotten"
    row = m._conn.execute("SELECT content_json, source_json FROM memory_items WHERE memory_id=?", (first,)).fetchone()
    assert row[0] == row[1] == "{}"


def test_forget_is_thorough(tmp_path):
    m = make(tmp_path)
    mid = m.add_candidate("life_event", content("Secret original summary", tags=["private"]), source())
    m.confirm(mid, "learner")
    m.forget(mid, "learner")
    assert m.recall("life_event") == []
    assert m.recall("life_event", query="original summary") == []
    row = m._conn.execute("SELECT * FROM memory_items WHERE memory_id=?", (mid,)).fetchone()
    assert row["content_json"] == row["source_json"] == "{}"
    assert all("Secret" not in str(value) and "original" not in str(value)
               for value in row)


def test_recall_filter_sort_query_and_counter(tmp_path):
    m = make(tmp_path)
    a = m.add_candidate("life_event", content("Alpha", tags=["Museum"]), source())
    b = m.add_candidate("life_event", content("Beta", tags=["Travel"]), source())
    m.confirm(a, "learner"); m.confirm(b, "learner")
    rejected = m.add_candidate("life_event", content("Hidden"), source()); m.reject(rejected, "learner")
    assert m.recall("life_event", query="museum", limit=2)[0]["memory_id"] == a
    rows = m.recall("life_event", limit=2)
    assert all(set(row) == {"memory_id", "kind", "content", "confirmed_at"} for row in rows)
    assert m.get(a)["status"] == "confirmed" and m.get(a)["recall_count"] == 2
    assert all(row["memory_id"] != rejected for row in rows)
    candidate = m.add_candidate("life_event", content("Candidate"), source())
    assert all(row["memory_id"] != candidate for row in m.recall("life_event", limit=20))
    for limit in (0, 21, True):
        with pytest.raises(LongTermMemoryError): m.recall("life_event", limit=limit)


def test_recall_sorting_and_read_only_views(tmp_path):
    times = iter([100, 100, 200, 200, 300, 300, 400, 400])
    m = LongTermMemory("a1-x", root=str(tmp_path), now=lambda: next(times),
                       id_factory=iter(["mem-b", "mem-a"]).__next__)
    b = m.add_candidate("life_event", content("B"), source())
    a = m.add_candidate("life_event", content("A"), source())
    m.confirm(b, "learner")
    m.confirm(a, "learner")
    before = {item["memory_id"]: item["recall_count"] for item in m.list_items()}
    assert [item["memory_id"] for item in m.recall("life_event")] == [a, b]
    after_recall = {item["memory_id"]: item["recall_count"] for item in m.list_items()}
    assert after_recall[a] == before[a] + 1 and after_recall[b] == before[b] + 1
    assert m.get(a)["recall_count"] == after_recall[a]
    assert [item["memory_id"] for item in m.list_items(status="confirmed")] == [b, a]
    with pytest.raises(LongTermMemoryError): m.list_items(status="invalid")


def test_isolation_and_validation(tmp_path):
    a = make(tmp_path, "a1-a")
    b = make(tmp_path, "a1-b")
    mid = a.add_candidate("life_event", content("Private"), source())
    assert b.get(mid) is None and b.list_items() == []
    with pytest.raises(LongTermMemoryError): b.confirm(mid, "learner")
    with pytest.raises(LongTermMemoryError): b.forget(mid, "learner")
    invalid = [
        ("bad", content("x"), source()),
        ("life_event", {"place": "x"}, source()),
        ("life_event", content("x", unknown="y"), source()),
        ("life_event", content("x", tags="no"), source()),
        ("life_event", content("x" * 201), source()),
        ("life_event", content("x"), {"extractor": "x" * 161}),
    ]
    for args in invalid:
        with pytest.raises(LongTermMemoryError): a.add_candidate(*args)


def test_permission_and_forgotten_confirm_rejections(tmp_path):
    m = make(tmp_path)
    mid = m.add_candidate("life_event", content("State"), source())
    with pytest.raises(LongTermMemoryError): m.reject(mid, "system")
    with pytest.raises(LongTermMemoryError): m.forget(mid, "system")
    assert m.get(mid)["status"] == "candidate"
    m.forget(mid, "learner")
    with pytest.raises(LongTermMemoryError): m.confirm(mid, "learner")


def test_cli_list_forget_and_errors(tmp_path, capsys):
    m = make(tmp_path)
    mid = m.add_candidate("life_event", content("CLI"), source())
    from engine.memory.long_term import _main
    assert _main(["list", "--learner-id", "a1-x", "--root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1
    assert _main(["forget", "--learner-id", "a1-x", "--memory-id", mid, "--root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "forgotten"
    assert _main(["forget", "--learner-id", "a1-x", "--memory-id", "missing", "--root", str(tmp_path)]) == 2
    assert capsys.readouterr().err


def test_secure_delete_and_forget_all_cli_isolated(tmp_path, capsys):
    marker = "P45-UNIQUE-" + ("Z" * 189)
    owner = LongTermMemory("owner", root=str(tmp_path), id_factory=iter(["owner-1", "owner-2", "owner-3", "owner-4"]).__next__)
    other = LongTermMemory("other", root=str(tmp_path), id_factory=iter(["other-1"]).__next__)
    confirmed = owner.add_candidate("life_event", content(marker), source())
    candidate = owner.add_candidate("unfinished_task", content("candidate"), source())
    rejected = owner.add_candidate("life_event", content("rejected"), source())
    tombstone = owner.add_candidate("life_event", content("old"), source())
    owner.confirm(confirmed, "learner")
    owner.reject(rejected, "learner")
    owner.forget(tombstone, "learner")
    other_id = other.add_candidate("life_event", content("other learner"), source())
    other.confirm(other_id, "learner")
    db = Path(tmp_path) / "coach.db"
    assert owner._conn.execute("PRAGMA secure_delete").fetchone()[0] in (1, "1")
    before_blobs = b"".join(path.read_bytes() for path in Path(tmp_path).glob("coach.db*"))
    assert marker.encode() in before_blobs
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert marker.encode() in db.read_bytes()
    from engine.memory.long_term import _main
    assert _main(["forget-all", "--learner-id", "owner", "--root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"learner_id": "owner", "forgotten": 3}
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    blobs = b"".join(path.read_bytes() for path in Path(tmp_path).glob("coach.db*"))
    assert marker.encode() not in blobs
    assert owner.recall("life_event") == []
    assert other.get(other_id)["status"] == "confirmed"
    assert all(owner.get(mid)["last_actor"] == "operator" for mid in (confirmed, candidate, rejected))
    assert _main(["forget-all", "--learner-id", "owner", "--root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["forgotten"] == 0


def test_secure_delete_off_is_a_real_negative_control(tmp_path):
    marker = "P45-OFF-" + ("Y" * 192)
    memory = LongTermMemory("off", root=str(tmp_path), id_factory=iter(["off-memory"]).__next__)
    memory._conn.execute("PRAGMA secure_delete=OFF")
    memory_id = memory.add_candidate("life_event", content(marker), source())
    memory.confirm(memory_id, "learner")
    memory.forget(memory_id, "learner")
    with sqlite3.connect(Path(tmp_path) / "coach.db") as conn:
        assert conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    blobs = b"".join(path.read_bytes() for path in Path(tmp_path).glob("coach.db*"))
    assert marker.encode() in blobs


def test_reapply_forgets_cli_and_source_is_read_only(tmp_path, capsys):
    from engine.local_backup import create_backup, restore_backup
    from engine.memory.long_term import _main
    root = Path(tmp_path)
    current = root / "current"
    restored = root / "restored"
    bundle = root / "backup"
    owner = LongTermMemory("owner", root=str(current), id_factory=iter(["owner-memory"]).__next__)
    other = LongTermMemory("other", root=str(current), id_factory=iter(["other-memory"]).__next__)
    owner_id = owner.add_candidate("life_event", content("recover me"), source())
    owner.confirm(owner_id, "learner")
    other_id = other.add_candidate("life_event", content("keep other"), source())
    other.confirm(other_id, "learner")
    create_backup(current, bundle)
    owner.forget(owner_id, "learner")
    other.forget(other_id, "learner")
    source_hash = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in current.glob("coach.db*") if not path.name.endswith("-shm")}
    restore_backup(bundle, restored)
    assert LongTermMemory("owner", root=str(restored)).get(owner_id)["status"] == "confirmed"
    assert _main(["reapply-forgets", "--learner-id", "owner", "--from", str(current), "--root", str(restored)]) == 0
    assert json.loads(capsys.readouterr().out) == {"learner_id": "owner", "reapplied": 1, "missing": 0}
    target = LongTermMemory("owner", root=str(restored))
    assert target.get(owner_id)["status"] == "forgotten"
    assert target.get(owner_id)["last_actor"] == "operator"
    assert target.recall("life_event") == []
    assert LongTermMemory("other", root=str(restored)).get(other_id)["status"] == "confirmed"
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in current.glob("coach.db*") if not path.name.endswith("-shm")} == source_hash
    assert _main(["reapply-forgets", "--learner-id", "owner", "--from", str(current), "--root", str(current)]) == 2
    assert "--from" in capsys.readouterr().err


def test_reapply_forgets_missing_and_invalid_source(tmp_path, capsys):
    from engine.memory.long_term import _main
    target = Path(tmp_path) / "target"
    make(target, "owner")
    assert _main(["reapply-forgets", "--learner-id", "owner", "--from", str(Path(tmp_path) / "missing"), "--root", str(target)]) == 2
    assert "不存在" in capsys.readouterr().err


def test_secure_delete_erases_bytes_after_checkpoint(tmp_path):
    marker = "P45-OFF-" + ("Y" * 192)
    memory = LongTermMemory("on", root=str(tmp_path), id_factory=lambda: "on-memory")
    try:
        mid = memory.add_candidate("life_event", content(marker), source())
        memory.confirm(mid, "learner")
        assert len(marker) == 200
        assert any(marker.encode() in path.read_bytes() for path in tmp_path.glob("coach.db*"))
        memory.forget(mid, "learner")
        assert tuple(memory._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()) == (0, 0, 0)
        assert all(marker.encode() not in path.read_bytes() for path in tmp_path.glob("coach.db*"))
        assert memory.get(mid)["content"] == {}
        assert memory.get(mid)["source"] == {}
        assert memory.recall("life_event") == []
    finally:
        memory._conn.close()


def test_reapply_forgets_missing_tombstones_and_idempotence(tmp_path, capsys):
    from engine.memory.long_term import _main
    current, restored = tmp_path / "current", tmp_path / "restored"
    owner = LongTermMemory("owner", root=str(current), id_factory=iter(["missing", "done", "foreign"]).__next__)
    target = LongTermMemory("owner", root=str(restored), id_factory=lambda: "done")
    other = LongTermMemory("other", root=str(restored), id_factory=lambda: "foreign")
    try:
        for summary in ("missing", "done", "foreign"):
            mid = owner.add_candidate("life_event", content(summary), source())
            owner.forget(mid, "operator")
        mid = target.add_candidate("life_event", content("done"), source())
        target.forget(mid, "operator")
        foreign = other.add_candidate("life_event", content("foreign"), source())
        other.confirm(foreign, "learner")
        args = ["reapply-forgets", "--learner-id", "owner", "--from", str(current), "--root", str(restored)]
        for _ in range(2):
            assert _main(args) == 0
            assert json.loads(capsys.readouterr().out) == {"learner_id": "owner", "reapplied": 0, "missing": 2}
            assert other.get(foreign)["status"] == "confirmed"
        assert _main(["reapply-forgets", "--learner-id", "nobody", "--from", str(current), "--root", str(restored)]) == 0
        assert json.loads(capsys.readouterr().out) == {"learner_id": "nobody", "reapplied": 0, "missing": 0}
    finally:
        owner._conn.close(); target._conn.close(); other._conn.close()


def test_reapply_invalid_paths_do_not_create_or_modify_databases(tmp_path, capsys):
    from engine.memory.long_term import _main
    from engine.local_backup import create_backup, restore_backup
    current, target = tmp_path / "current", tmp_path / "target"
    memory = LongTermMemory("owner", root=str(current))
    memory._conn.close()
    create_backup(current, tmp_path / "bundle")
    restore_backup(tmp_path / "bundle", target)
    args = ["reapply-forgets", "--learner-id", "owner", "--root", str(target)]
    assert _main(args) == 2
    assert not capsys.readouterr().out
    assert _main(args + ["--from", str(target / ".." / "target")]) == 2
    assert not capsys.readouterr().out
    assert _main(args + ["--from", str(tmp_path / "absent")]) == 2
    assert not (tmp_path / "absent").exists()
    capsys.readouterr()
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "coach.db").write_bytes(b"not sqlite: private-marker")
    before = (broken / "coach.db").read_bytes()
    assert _main(args + ["--from", str(broken)]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "private-marker" not in output.err
    assert (broken / "coach.db").read_bytes() == before
    assert _main(["reapply-forgets", "--learner-id", "owner", "--from", str(current), "--root", str(tmp_path / "missing-target")]) == 2
    assert not (tmp_path / "missing-target").exists()
    capsys.readouterr()
    assert _main(["reapply-forgets", "--learner-id", "owner", "--from", str(current), "--root", str(broken)]) == 2
    assert capsys.readouterr().out == ""
    assert (broken / "coach.db").read_bytes() == before

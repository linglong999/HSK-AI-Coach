import json
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

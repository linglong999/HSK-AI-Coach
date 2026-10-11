import json
import sqlite3
from unittest.mock import Mock, patch

import pytest

from engine.memory import store
from engine.memory.learner_memory import LearnerMemory
from engine.memory.long_term import LongTermMemory, LongTermMemoryError
from engine.memory.opening import build_opening, compile_memory_brief
from engine.scenarios import load_scenarios
from engine.study import StudyTracker
from tests.test_memory_extractor import FakeClient
from tests.test_study_partition import seed


@pytest.fixture
def memories(tmp_path):
    mem = LearnerMemory("learner", root=str(tmp_path))
    ltm = LongTermMemory("learner", root=str(tmp_path))
    yield mem, ltm
    mem._conn.close()
    ltm._conn.close()


def opening(memories, factory, enabled=True):
    return build_opening(*memories, "new", enabled=enabled, client_factory=factory)


def test_disabled_reads_nothing():
    mem, ltm, factory = Mock(), Mock(), Mock()
    out = build_opening(mem, ltm, "new", enabled=False, client_factory=factory)
    assert out == {"enabled": False, "resume": None, "candidates": [], "notices": []}
    assert mem.mock_calls == ltm.mock_calls == factory.mock_calls == []


def test_disabled_with_previous_experience_never_calls_factory(memories):
    mem, _ = memories
    mem.append("prev", "user", "我上周去了长城")
    factory = Mock(return_value=FakeClient())
    assert opening(memories, factory, enabled=False)["candidates"] == []
    factory.assert_not_called()
    assert mem.previous_session("new")["memory_scanned_at"] is None


def test_once_user_only_and_source(memories):
    mem, ltm = memories
    mem.append("prev", "user", "我上周去了长城")
    mem.append("prev", "assistant", "ASSISTANT_SECRET 我昨天去了医院")
    client = FakeClient()
    factory = Mock(return_value=client)
    first = opening(memories, factory)
    second = opening(memories, factory)
    assert len(client.calls) == 1
    factory.assert_called_once()
    assert "ASSISTANT_SECRET" not in client.calls[0]["user"]
    assert json.loads(client.calls[0]["user"])["user_texts"] == ["我上周去了长城"]
    assert first == second
    assert set(first["candidates"][0]) == {"memory_id", "kind", "content", "created_at"}
    row = ltm.get(first["candidates"][0]["memory_id"])
    assert row["status"] == "candidate"
    assert row["source"] == {"conversation_id": "prev", "extractor": "p42-v1", "event_key": "prev:0"}
    assert mem.previous_session("new")["memory_scanned_at"] is not None


def test_no_provider_then_retry(memories):
    mem, _ = memories
    mem.append("prev", "user", "我上周去了长城")
    out = opening(memories, lambda: None)
    assert out["notices"] == [{"stage": "memory_extract", "reason": "no_provider"}]
    assert mem.previous_session("new")["memory_scanned_at"] is None
    assert len(opening(memories, lambda: FakeClient())["candidates"]) == 1


@pytest.mark.parametrize("failure", ["client", "factory", "extractor"])
def test_extract_failure_not_marked(memories, failure):
    mem, _ = memories
    mem.append("prev", "user", "我上周去了长城")
    factory = Mock(return_value=FakeClient(error=RuntimeError("offline")))
    if failure == "factory":
        factory.side_effect = RuntimeError("factory failed")
    with patch("engine.memory.opening.extract_candidates", side_effect=RuntimeError("fail")) if failure == "extractor" else patch("engine.memory.opening.EXTRACTOR_VERSION", "p42-v1"):
        out = opening(memories, factory)
    assert out["notices"] == [{"stage": "memory_extract", "reason": "extract_failed"}]
    assert mem.previous_session("new")["memory_scanned_at"] is None


@pytest.mark.parametrize("texts,result", [(["这个怎么说？"], None), (["我去了长城"], {"candidates": []})])
def test_empty_success_marks(memories, texts, result):
    mem, _ = memories
    for text in texts:
        mem.append("prev", "user", text)
    factory = Mock(return_value=FakeClient(result))
    assert opening(memories, factory)["notices"] == []
    assert mem.previous_session("new")["memory_scanned_at"] is not None
    if result is None:
        factory.assert_not_called()


@pytest.mark.parametrize("scene_kind", ["valid", "empty", "unknown"])
def test_resume(memories, scene_kind):
    mem, _ = memories
    scene = load_scenarios()[0]
    scene_id = scene["scene_id"] if scene_kind == "valid" else ("" if scene_kind == "empty" else "missing")
    mem.append("prev", "user", "这个怎么说？")
    mem.touch("prev", scene_id=scene_id, bump=False)
    factory = Mock(side_effect=AssertionError("no LLM"))
    out = opening(memories, factory)
    assert out["resume"] == ({"scene_id": scene_id, "title": scene["title"], "title_zh": scene["title_zh"],
                              "conversation_id": "prev"} if scene_kind == "valid" else None)
    factory.assert_not_called()


def test_single_invalid_candidate_skipped(memories):
    mem, ltm = memories
    mem.append("prev", "user", "我昨天去了北京")
    client = FakeClient({"candidates": [{"summary": "坏", "tags": "bad"}, {"summary": "北京"}]})
    out = opening(memories, lambda: client)
    assert [row["content"]["summary"] for row in out["candidates"]] == ["北京"]
    assert ltm.list_items()[0]["source"]["event_key"] == "prev:1"
    assert mem.previous_session("new")["memory_scanned_at"] is not None


def test_previous_session_partition_order_and_no_bump(memories, tmp_path):
    mem, _ = memories
    for sid in ("z", "a", "current"):
        mem.append(sid, "user", sid)
    mem.append("assistant-only", "assistant", "ignore")
    other = LearnerMemory("other", root=str(tmp_path))
    other.append("foreign", "user", "ignore")
    other._conn.close()
    mem._conn.execute("UPDATE sessions SET updated_at=10")
    mem._conn.commit()
    mem.touch("a", scene_id="x" * 100, bump=False)
    mem.mark_memory_scanned("a")
    row = mem.previous_session("current")
    assert row["session_id"] == "a"
    assert row["scene_id"] == "x" * 80
    assert row["updated_at"] == 10
    assert row["memory_scanned_at"] is not None
    assert set(row) == {"session_id", "scene_id", "title", "updated_at", "memory_scanned_at"}
    mem._conn.execute("UPDATE sessions SET updated_at=20 WHERE session_id='z'")
    mem._conn.commit()
    assert mem.previous_session("current")["session_id"] == "z"


def test_old_sessions_schema_migration_preserves_data(tmp_path):
    conn = sqlite3.connect(tmp_path / "coach.db")
    conn.execute("CREATE TABLE sessions (learner_id TEXT NOT NULL, session_id TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active', pinned INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (learner_id,session_id))")
    conn.execute("INSERT INTO sessions VALUES ('old','prev',1,2,'旧标题','active',1)")
    conn.commit()
    conn.close()
    migrated = store.ensure_store(str(tmp_path))
    columns = {row[1]: row for row in migrated.execute("PRAGMA table_info(sessions)")}
    assert columns["scene_id"][2:5] == ("TEXT", 1, "''")
    assert columns["memory_scanned_at"][2] == "INTEGER"
    assert tuple(migrated.execute("SELECT learner_id,session_id,created_at,updated_at,title,status,pinned,scene_id,memory_scanned_at FROM sessions").fetchone()) == ("old", "prev", 1, 2, "旧标题", "active", 1, "", None)
    assert migrated.execute("SELECT incognito FROM sessions").fetchone()[0] == 0
    assert columns["incognito"][2:5] == ("INTEGER", 1, "0")
    migrated.close()


def test_compile_brief_language_and_no_internal_fields():
    item = {"content": {"summary": "去了长城", "place": "北京", "when": "上周"}, "source": "secret", "dedup_key": "secret2"}
    assert compile_memory_brief([]) == ""
    for lang in ("zh", "en"):
        brief = compile_memory_brief([item], lang)
        assert brief.startswith("[Memory]")
        assert "- 去了长城 (北京/上周)" in brief
        assert "secret" not in brief
    assert "不要当作考题" in compile_memory_brief([item], "zh")
    assert "treat it as a test" in compile_memory_brief([item], "en")


def test_forget_excluded_from_opening_and_recall(memories):
    mem, ltm = memories
    mem.append("prev", "user", "我上周去了长城")
    factory = lambda: FakeClient()
    memory_id = opening(memories, factory)["candidates"][0]["memory_id"]
    ltm.confirm(memory_id, "learner")
    assert "长城" in compile_memory_brief(ltm.recall("life_event"))
    ltm.forget(memory_id, "learner")
    assert opening(memories, factory)["candidates"] == []
    assert ltm.recall("life_event") == []


def test_purge_real_memory_and_scene(tmp_path):
    tracker = StudyTracker(root=str(tmp_path))
    tracker.create_session(learner_id="a1-memory")
    seed(tmp_path, "a1-memory")
    mem = LearnerMemory("a1-memory", root=str(tmp_path))
    mem.touch("c1", scene_id=load_scenarios()[0]["scene_id"], bump=False)
    mem.append("c1", "user", "我上周去了长城")
    ltm = LongTermMemory("a1-memory", root=str(tmp_path))
    assert build_opening(mem, ltm, "new", enabled=True, client_factory=lambda: FakeClient())["candidates"]
    counts = tracker.learner_data_counts("a1-memory")
    assert counts["sessions"] == counts["memory_items"] == 1
    tracker.purge_learner("a1-memory")
    after = tracker.learner_data_counts("a1-memory")
    for key in ("ledger_events", "sessions", "messages", "profiles", "memory_items", "study_sessions"):
        assert after[key] == 0
    assert after["files"] == []
    mem._conn.close()
    ltm._conn.close()
    tracker._conn.close()


def test_incognito_previous_session_excluded(memories):
    mem, _ = memories
    mem.append("private", "user", "我上周去了长城")
    mem.touch("private", scene_id=load_scenarios()[0]["scene_id"], incognito=True, bump=False)
    factory = Mock(return_value=FakeClient())
    assert mem.previous_session("new") is None
    assert opening(memories, factory) == {"enabled": True, "resume": None, "candidates": [], "notices": []}
    factory.assert_not_called()
    assert mem._conn.execute("SELECT memory_scanned_at FROM sessions WHERE session_id='private'").fetchone()[0] is None
    mem.append("normal", "user", "这个怎么说？")
    mem._conn.execute("UPDATE sessions SET updated_at=100 WHERE session_id='private'")
    mem._conn.execute("UPDATE sessions SET updated_at=10 WHERE session_id='normal'")
    mem._conn.commit()
    assert mem.previous_session("new")["session_id"] == "normal"


def test_incognito_session_meta_isolation_and_no_bump(memories, tmp_path):
    mem, _ = memories
    assert mem.session_meta("missing") is None
    assert not mem.has_session("missing")
    mem.touch("same", scene_id="restaurant", incognito=True, bump=False)
    mem._conn.execute("UPDATE sessions SET updated_at=12")
    mem._conn.commit()
    mem.touch("same", incognito=True, bump=False)
    assert mem.session_meta("same") == {"scene_id": "restaurant", "incognito": True}
    row = mem.list_sessions()[0]
    assert row["updated_at"] == 12 and row["incognito"] is True and row["scene_id"] == "restaurant"
    other = LearnerMemory("other", root=str(tmp_path))
    assert other.session_meta("same") is None
    other.touch("same")
    assert other.session_meta("same") == {"scene_id": "", "incognito": False}
    assert mem.session_meta("same")["incognito"] is True
    other._conn.close()


def test_p42_schema_incognito_migration_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / "coach.db")
    conn.execute("CREATE TABLE sessions (learner_id TEXT,session_id TEXT,created_at INTEGER,updated_at INTEGER,title TEXT,status TEXT,pinned INTEGER,scene_id TEXT,memory_scanned_at INTEGER,PRIMARY KEY(learner_id,session_id))")
    conn.execute("INSERT INTO sessions VALUES ('old','c',1,2,'title','active',0,'restaurant',3)")
    conn.commit(); conn.close()
    for _ in range(2):
        migrated = store.ensure_store(str(tmp_path))
        assert tuple(migrated.execute("SELECT * FROM sessions").fetchone()) == ('old', 'c', 1, 2, 'title', 'active', 0, 'restaurant', 3, 0)
        migrated.close()

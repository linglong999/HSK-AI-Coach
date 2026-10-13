"""P1-5 隔离目录演练：SQLite、JSON、哈希、无覆盖恢复。"""

import json
from pathlib import Path
import sqlite3
import tempfile
import shutil
from contextlib import closing

import pytest

from engine.local_backup import create_backup, inspect_source, restore_backup, verify_backup
from engine import providers
from engine.memory.long_term import LongTermMemory


def _source(root: Path) -> Path:
    data = root / "data"
    data.mkdir()
    with closing(sqlite3.connect(data / "coach.db")) as conn:
        with conn:
            conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, note TEXT)")
            conn.execute("INSERT INTO sessions VALUES (?, ?)", ("demo", "学习记录"))
    (data / "graph_demo.json").write_text('{"nodes":[1]}', encoding="utf-8")
    (data / ".env").write_text("DUMMY=do-not-back-up", encoding="utf-8")
    providers.save_store(str(data), {"providers": [providers.new_provider(
        "T", "https://8.8.8.8/v1", "dummy-key", "m")], "default_id": None})
    (data / "nested").mkdir()
    (data / "nested" / "excluded.txt").write_text("not production data", encoding="utf-8")
    return data


def test_backup_verify_restore_without_overwrite():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = _source(root)
        bundle = root / "snapshot"
        restored = root / "restored"
        manifest = create_backup(source, bundle)
        assert manifest["excluded_paths"] == [".env", "nested"]
        assert not manifest["complete_for_source"]
        assert {item["name"] for item in manifest["files"]} == {
            "coach.db", "graph_demo.json", "llm_providers.json"}
        assert verify_backup(bundle)["files"] == manifest["files"]
        restore_backup(bundle, restored)
        with closing(sqlite3.connect(restored / "coach.db")) as conn:
            assert conn.execute("SELECT note FROM sessions WHERE id='demo'").fetchone()[0] == "学习记录"
        assert json.loads((restored / "graph_demo.json").read_text(encoding="utf-8"))["nodes"] == [1]
        assert providers.load_store(str(restored))["providers"][0]["api_key"] == "dummy-key"
        assert not (restored / "nested").exists()
        assert not (restored / ".env").exists()
        with pytest.raises(ValueError, match="不存在"):
            restore_backup(bundle, restored)


def test_tamper_rejected_before_restore():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = root / "snapshot"
        create_backup(_source(root), bundle)
        (bundle / "graph_demo.json").write_text("tampered", encoding="utf-8")
        with pytest.raises(ValueError, match="校验失败"):
            restore_backup(bundle, root / "restored")
        assert not (root / "restored").exists()


def test_inspect_reports_uncovered_paths_without_writing():
    with tempfile.TemporaryDirectory() as tmp:
        source = _source(Path(tmp))
        before = {path.name for path in source.iterdir()}
        scope = inspect_source(source)
        assert scope["excluded"] == [".env", "nested"]
        assert set(scope["included"]) == before - {".env", "nested"}
        assert not scope["complete_for_source"]
        assert {path.name for path in source.iterdir()} == before


def test_restore_rechecks_copied_files_before_publishing(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = root / "snapshot"
        create_backup(_source(root), bundle)
        destination = root / "restored"
        real_copy = shutil.copy2

        def corrupt_copy(source, target, *args, **kwargs):
            result = real_copy(source, target, *args, **kwargs)
            if Path(source).name == "graph_demo.json":
                Path(target).write_text("corrupted", encoding="utf-8")
            return result

        monkeypatch.setattr("engine.local_backup.shutil.copy2", corrupt_copy)
        with pytest.raises(ValueError, match="校验失败"):
            restore_backup(bundle, destination)
        assert not destination.exists()


def test_path_traversal_and_unlisted_file_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = root / "snapshot"
        create_backup(_source(root), bundle)
        path = bundle / "backup_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["files"][0]["name"] = "../escape"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(ValueError, match="无效文件名"):
            verify_backup(bundle)


def test_isolated_rotation_delete_and_restore_drill():
    """仅处理临时虚构数据；供应商侧撤销不在本机测试范围内。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = _source(root)
        bundle = root / "before-rotation"
        create_backup(source, bundle)

        store = providers.load_store(str(source))
        old_id = store["providers"][0]["id"]
        replacement = providers.new_provider(
            "Replacement", "https://8.8.8.8/v1", "replacement-dummy-key", "m")
        store["providers"] = [replacement]
        store["default_id"] = replacement["id"]
        providers.save_store(str(source), store)
        current = providers.load_store(str(source))
        assert current["default_id"] == replacement["id"]
        assert all(p["id"] != old_id for p in current["providers"])

        # 删除演练前确认目标就是本测试创建的临时 data/，绝不碰真实数据。
        assert source.parent == root and source.name == "data"
        shutil.rmtree(source)
        assert not source.exists()
        assert verify_backup(bundle)["files"]
        restored = root / "restored-data"
        restore_backup(bundle, restored)
        assert providers.load_store(str(restored))["providers"][0]["api_key"] == "dummy-key"


def test_backup_contains_no_forgotten_memory_marker(tmp_path):
    root = Path(tmp_path)
    source = root / "data"
    marker = "BACKUP-P45-" + ("Q" * 189)
    memory = LongTermMemory("owner", root=str(source))
    memory_id = memory.add_candidate("life_event", {"summary": marker}, {"conversation_id": "c", "extractor": "test", "event_key": "e"})
    memory.confirm(memory_id, "learner")
    assert len(marker) == 200
    assert any(marker.encode() in path.read_bytes() for path in source.glob("coach.db*"))
    memory.forget(memory_id, "learner")
    with sqlite3.connect(source / "coach.db") as conn:
        assert conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    bundle = root / "backup"
    create_backup(source, bundle)
    assert all(marker.encode() not in path.read_bytes() for path in bundle.iterdir() if path.is_file())
    memory._conn.close()


def test_restore_old_backup_then_reapply_forgets(tmp_path, capsys):
    from engine.memory.long_term import _main
    import hashlib
    current, restored = tmp_path / "current", tmp_path / "restored"
    owner = LongTermMemory("owner", root=str(current), id_factory=lambda: "old-memory")
    other = LongTermMemory("other", root=str(current), id_factory=lambda: "other-memory")
    try:
        mid = owner.add_candidate("life_event", {"summary": "old experience"}, {})
        oid = other.add_candidate("life_event", {"summary": "other experience"}, {})
        owner.confirm(mid, "learner"); other.confirm(oid, "learner")
        create_backup(current, tmp_path / "backup-a")
        owner.forget(mid, "learner")
        assert tuple(owner._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()) == (0, 0, 0)
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in current.glob("coach.db*") if not p.name.endswith("-shm")}
        restore_backup(tmp_path / "backup-a", restored)
        target = LongTermMemory("owner", root=str(restored))
        foreign = LongTermMemory("other", root=str(restored))
        try:
            assert target.get(mid)["status"] == "confirmed"
            assert _main(["reapply-forgets", "--learner-id", "owner", "--from", str(current), "--root", str(restored)]) == 0
            assert json.loads(capsys.readouterr().out) == {"learner_id": "owner", "reapplied": 1, "missing": 0}
            assert target.get(mid)["status"] == "forgotten"
            assert target.get(mid)["content"] == target.get(mid)["source"] == {}
            assert target.recall("life_event") == []
            assert foreign.get(oid)["status"] == "confirmed"
            assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in current.glob("coach.db*") if not p.name.endswith("-shm")} == before
        finally:
            target._conn.close(); foreign._conn.close()
    finally:
        owner._conn.close(); other._conn.close()

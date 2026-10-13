"""P4.1 长期经历记忆数据层。"""

import argparse
import hashlib
import json
import re
import secrets
import os
import sqlite3
from pathlib import Path
from contextlib import closing
import time
from typing import Callable, Optional

from engine.memory import store

MEMORY_KINDS = ("life_event", "unfinished_task")
MEMORY_STATES = ("candidate", "confirmed", "rejected", "forgotten")
CONFIRM_ACTORS = ("learner", "operator")
CONTENT_FIELDS = {
    "life_event": {"summary", "place", "when", "tags"},
    "unfinished_task": {"summary", "scene_id"},
}
SOURCE_FIELDS = {"conversation_id", "extractor", "event_key"}
MAX_TEXT = 200
MAX_TAGS = 10
RECALL_LIMIT_MAX = 20


class LongTermMemoryError(ValueError):
    pass


class LongTermMemory:
    def __init__(self, learner_id: str, root: str = "data",
                 now: Optional[Callable[[], int]] = None,
                 id_factory: Optional[Callable[[], str]] = None):
        if not isinstance(learner_id, str) or not learner_id.strip():
            raise LongTermMemoryError("learner_id 无效")
        self.learner_id = learner_id
        self.root = root
        self._conn = store.ensure_store(root)
        self._now_fn = now or (lambda: int(time.time()))
        self._id_factory = id_factory or (lambda: "mem-" + secrets.token_hex(10))

    def _now(self):
        try:
            return int(self._now_fn())
        except (TypeError, ValueError):
            raise LongTermMemoryError("时间无效")

    def _validate_content(self, kind, content):
        if kind not in MEMORY_KINDS:
            raise LongTermMemoryError("kind 无效")
        if not isinstance(content, dict) or set(content) - CONTENT_FIELDS[kind]:
            raise LongTermMemoryError("content 字段无效")
        if not isinstance(content.get("summary"), str) or not content["summary"].strip():
            raise LongTermMemoryError("summary 必填")
        out = {}
        for key, value in content.items():
            if key == "tags":
                if (not isinstance(value, list) or len(value) > MAX_TAGS
                        or not all(isinstance(tag, str) and len(tag.strip()) <= 40 for tag in value)):
                    raise LongTermMemoryError("tags 无效")
                out[key] = [tag.strip() for tag in value]
            else:
                if not isinstance(value, str) or len(value.strip()) > MAX_TEXT:
                    raise LongTermMemoryError(f"{key} 无效")
                out[key] = value.strip()
        return out

    @staticmethod
    def _validate_source(source):
        if not isinstance(source, dict) or set(source) - SOURCE_FIELDS:
            raise LongTermMemoryError("source 字段无效")
        out = {}
        for key, value in source.items():
            if not isinstance(value, str) or len(value) > 160:
                raise LongTermMemoryError("source 值无效")
            out[key] = value
        return out

    def _dedup_key(self, kind, content):
        normalized = dict(content)
        normalized["summary"] = re.sub(r"\s+", " ", normalized["summary"].strip().lower())
        norm = json.dumps({"kind": kind, **normalized}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]

    def _row(self, row, include_source=True):
        if row is None:
            return None
        out = {"memory_id": row["memory_id"], "learner_id": row["learner_id"],
               "kind": row["kind"], "status": row["status"],
               "content": json.loads(row["content_json"]),
               "source": json.loads(row["source_json"]),
               "dedup_key": row["dedup_key"], "created_at": row["created_at"],
               "updated_at": row["updated_at"], "confirmed_at": row["confirmed_at"],
               "last_recalled_at": row["last_recalled_at"],
               "recall_count": row["recall_count"], "last_actor": row["last_actor"]}
        if not include_source:
            out.pop("source", None)
        return out

    def add_candidate(self, kind: str, content: dict, source: dict) -> str:
        clean = self._validate_content(kind, content)
        source_clean = self._validate_source(source)
        dedup = self._dedup_key(kind, clean)
        existing = self._conn.execute(
            "SELECT memory_id FROM memory_items WHERE learner_id=? AND dedup_key=?",
            (self.learner_id, dedup)).fetchone()
        if existing:
            return existing[0]
        memory_id = str(self._id_factory())
        if not memory_id:
            raise LongTermMemoryError("memory_id 无效")
        now = self._now()
        try:
            self._conn.execute(
                "INSERT INTO memory_items (memory_id, learner_id, kind, status, content_json, source_json, dedup_key, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (memory_id, self.learner_id, kind, "candidate",
                 json.dumps(clean, ensure_ascii=False), json.dumps(source_clean, ensure_ascii=False),
                 dedup, now, now))
            self._conn.commit()
        except Exception as exc:
            self._conn.rollback()
            raise LongTermMemoryError("memory_id 或去重键冲突") from exc
        return memory_id

    def _transition(self, memory_id, actor, allowed, target):
        if actor not in CONFIRM_ACTORS:
            raise LongTermMemoryError("actor 无权限")
        row = self._conn.execute(
            "SELECT * FROM memory_items WHERE learner_id=? AND memory_id=?",
            (self.learner_id, memory_id)).fetchone()
        if row is None:
            raise LongTermMemoryError("记忆不存在")
        if row["status"] == target or (target == "forgotten" and row["status"] == "forgotten"):
            return
        if row["status"] not in allowed:
            raise LongTermMemoryError("状态不允许该操作")
        now = self._now()
        if target == "confirmed":
            self._conn.execute("UPDATE memory_items SET status=?, confirmed_at=?, updated_at=?, last_actor=? WHERE learner_id=? AND memory_id=?",
                                (target, now, now, actor, self.learner_id, memory_id))
        elif target == "forgotten":
            self._conn.execute("UPDATE memory_items SET status=?, content_json='{}', source_json='{}', updated_at=?, last_actor=? WHERE learner_id=? AND memory_id=?",
                                (target, now, actor, self.learner_id, memory_id))
        else:
            self._conn.execute("UPDATE memory_items SET status=?, updated_at=?, last_actor=? WHERE learner_id=? AND memory_id=?",
                                (target, now, actor, self.learner_id, memory_id))
        self._conn.commit()

    def confirm(self, memory_id: str, actor: str) -> None:
        self._transition(memory_id, actor, {"candidate", "confirmed"}, "confirmed")

    def reject(self, memory_id: str, actor: str) -> None:
        self._transition(memory_id, actor, {"candidate", "rejected"}, "rejected")

    def forget(self, memory_id: str, actor: str) -> None:
        self._transition(memory_id, actor, {"candidate", "confirmed", "rejected"}, "forgotten")

    @staticmethod
    def _matches(item, query):
        if not query:
            return True
        q = query.casefold()
        return q in item["content"].get("summary", "").casefold() or any(
            q in tag.casefold() for tag in item["content"].get("tags", []))

    def recall(self, kind: str, query: str = "", limit: int = 5) -> list[dict]:
        if kind not in MEMORY_KINDS or type(limit) is not int or not 1 <= limit <= RECALL_LIMIT_MAX:
            raise LongTermMemoryError("recall 参数无效")
        rows = self._conn.execute(
            "SELECT * FROM memory_items WHERE learner_id=? AND kind=? AND status='confirmed' ORDER BY updated_at DESC, memory_id ASC",
            (self.learner_id, kind)).fetchall()
        result = []
        for row in rows:
            item = self._row(row)
            if not self._matches(item, query):
                continue
            now = self._now()
            self._conn.execute("UPDATE memory_items SET last_recalled_at=?, recall_count=recall_count+1 WHERE learner_id=? AND memory_id=?",
                               (now, self.learner_id, row["memory_id"]))
            result.append({"memory_id": row["memory_id"], "kind": row["kind"],
                           "content": item["content"], "confirmed_at": row["confirmed_at"]})
            if len(result) >= limit:
                break
        self._conn.commit()
        return result

    def get(self, memory_id: str):
        row = self._conn.execute("SELECT * FROM memory_items WHERE learner_id=? AND memory_id=?",
                                 (self.learner_id, memory_id)).fetchone()
        return self._row(row)

    def list_items(self, status: Optional[str] = None) -> list[dict]:
        if status is not None and status not in MEMORY_STATES:
            raise LongTermMemoryError("status 无效")
        if status is None:
            rows = self._conn.execute("SELECT * FROM memory_items WHERE learner_id=? ORDER BY created_at ASC",
                                      (self.learner_id,)).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM memory_items WHERE learner_id=? AND status=? ORDER BY created_at ASC",
                                      (self.learner_id, status)).fetchall()
        return [self._row(row) for row in rows]


def _main(argv=None):
    parser = argparse.ArgumentParser(description="长期经历记忆")
    parser.add_argument("command", choices=("list", "forget", "forget-all", "reapply-forgets"))
    parser.add_argument("--learner-id", required=True)
    parser.add_argument("--memory-id", default="")
    parser.add_argument("--status", default=None)
    parser.add_argument("--root", default="data")
    parser.add_argument("--from", dest="from_root", default="")
    args = parser.parse_args(argv)
    memory = None
    try:
        if args.command == "reapply-forgets":
            if not args.from_root:
                raise LongTermMemoryError("reapply-forgets 需要 --from")
            source = Path(args.from_root).resolve(strict=False)
            target = Path(args.root).resolve(strict=False)
            if source == target:
                raise LongTermMemoryError("--from 与 --root 必须是不同路径")
            source_db = source / store.DB_NAME
            target_db = target / store.DB_NAME
            if not source_db.is_file() or not target_db.is_file():
                raise LongTermMemoryError("源库或恢复库不存在")
            if os.path.samefile(source_db, target_db):
                raise LongTermMemoryError("--from 与 --root 必须是不同数据库")
            with closing(sqlite3.connect(target_db.as_uri() + "?mode=ro", uri=True)) as conn:
                conn.execute("SELECT memory_id, status FROM memory_items LIMIT 0")
            try:
                with closing(sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True)) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        "SELECT memory_id FROM memory_items WHERE learner_id=? AND status='forgotten'",
                        (args.learner_id,)).fetchall()
                    forgotten_ids = [str(row["memory_id"]) for row in rows]
            except (OSError, sqlite3.Error) as exc:
                raise LongTermMemoryError("源库不可读") from exc
            memory = LongTermMemory(args.learner_id, root=str(target))
            reapplied = 0
            missing = 0
            for memory_id in forgotten_ids:
                item = memory.get(memory_id)
                if item is None:
                    missing += 1
                elif item["status"] != "forgotten":
                    memory.forget(memory_id, "operator")
                    reapplied += 1
            print(json.dumps({"learner_id": args.learner_id, "reapplied": reapplied,
                              "missing": missing}, ensure_ascii=False))
            return 0

        memory = LongTermMemory(args.learner_id, root=args.root)
        if args.command == "list":
            items = memory.list_items(args.status)
            print(json.dumps({"learner_id": args.learner_id, "count": len(items), "items": items}, ensure_ascii=False))
        elif args.command == "forget":
            if not args.memory_id:
                raise LongTermMemoryError("forget 需要 --memory-id")
            memory.forget(args.memory_id, "operator")
            print(json.dumps({"memory_id": args.memory_id, "status": "forgotten"}, ensure_ascii=False))
        else:
            forgotten = 0
            for item in memory.list_items():
                if item["status"] != "forgotten":
                    memory.forget(item["memory_id"], "operator")
                    forgotten += 1
            print(json.dumps({"learner_id": args.learner_id, "forgotten": forgotten}, ensure_ascii=False))
        return 0
    except (OSError, sqlite3.Error):
        parser._print_message("数据库或路径操作失败\n", __import__("sys").stderr)
        return 2
    except LongTermMemoryError as exc:
        parser._print_message(f"{exc}\n", __import__("sys").stderr)
        return 2
    finally:
        if memory is not None:
            memory._conn.close()


if __name__ == "__main__":
    raise SystemExit(_main())

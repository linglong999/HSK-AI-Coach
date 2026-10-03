"""A1 单机试用记录模块。

`StudyTracker` 是试用数据的深模块：调用方只处理匿名编号和结构化事件，
SQLite 表、状态机、白名单、幂等、聚合和删除都隐藏在本模块内。它不保存
原始学习者文本、身份信息、API Key 或异常堆栈。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sqlite3
import time
from typing import Any, Callable, Dict, Iterable, Optional

from engine.memory import store


PROTOCOL_VERSION = "A1-2026-09-30-v1"
DEFAULT_RETENTION_DAYS = 90
SESSION_STATES = frozenset({"created", "consented", "active", "completed", "withdrawn"})
RECORDABLE_STATES = frozenset({"consented", "active"})

# 事件是可审计的产品行为，而不是原始日志。每种事件只接受这里列出的字段。
EVENT_FIELDS = {
    "input_observed": {"conversation_ref", "text_chars", "scene_type"},
    "intervention_decided": {
        "conversation_ref", "level", "reason", "error_count", "uncertain_count", "scanned",
    },
    "feedback_presented": {"conversation_ref", "strategies", "strategy_count"},
    "learning_confirmed": {"conversation_ref", "verdict", "covered_points", "total_points", "degraded", "source", "kp_id", "target_source", "graph_applied"},
    "learning_pending": {"conversation_ref", "verdict", "covered_points", "total_points", "degraded", "source", "kp_id", "target_source", "graph_applied"},
    "learning_rejected": {"conversation_ref", "verdict", "covered_points", "total_points", "degraded", "source", "kp_id", "target_source", "graph_applied"},
    "writeback_completed": {
        "conversation_ref", "retell_pass_count", "error_count", "notice_count",
    },
    "review_queue_observed": {"conversation_ref", "queue_count"},
    "dialog_completed": {
        "conversation_ref", "status", "fallback", "steps", "degraded_count",
    },
    "dialog_failed": {"conversation_ref", "stage", "reason"},
}

_REF_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_EVENT_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


class StudyError(ValueError):
    """试用记录的可预期业务错误。"""


class StudyNotRecordable(StudyError):
    """会话尚未同意、已完成或已撤回。"""


def reference(value: str) -> str:
    """将会话引用变成稳定、不可逆的短引用，避免把用户自选 ID 写入试用表。"""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24]


class StudyTracker:
    """A1 匿名试用状态、事件、聚合和删除的唯一接口。"""

    def __init__(self, root: str = "data", now: Optional[Callable[[], int]] = None,
                 id_factory: Optional[Callable[[], str]] = None,
                 retention_days: int = DEFAULT_RETENTION_DAYS):
        if retention_days < 1 or retention_days > 3650:
            raise ValueError("retention_days 必须在 1 到 3650 之间")
        self.root = root
        self._conn = store.ensure_store(root)
        self._now = now or (lambda: int(time.time()))
        self._id_factory = id_factory or (lambda: "A1-" + secrets.token_hex(10))
        self._retention_days = retention_days

    def create_session(self, protocol_version: str = PROTOCOL_VERSION,
                       scene_type: str = "general") -> str:
        protocol = self._text(protocol_version, "protocol_version", 80)
        scene = self._text(scene_type, "scene_type", 80)
        study_id = self._text(self._id_factory(), "study_session_id", 80)
        if not _EVENT_KEY_RE.fullmatch(study_id):
            raise ValueError("study_session_id 含非法字符")
        now = self._now()
        try:
            self._conn.execute(
                "INSERT INTO study_sessions"
                " (study_session_id, protocol_version, scene_type, status,"
                "  created_at, retention_until) VALUES (?,?,?,?,?,?)",
                (study_id, protocol, scene, "created", now,
                 now + self._retention_days * 86400))
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise StudyError("study_session_id 已存在") from exc
        return study_id

    def consent(self, study_session_id: str, consent_version: str) -> None:
        study_id = self._id(study_session_id)
        version = self._text(consent_version, "consent_version", 80)
        row = self._session(study_id)
        if row is None:
            raise StudyError("study_session_id 不存在")
        state = row["status"]
        if state == "withdrawn":
            raise StudyError("已撤回的试用不能重新同意")
        if state in {"consented", "active"}:
            existing = self._conn.execute(
                "SELECT consent_version FROM study_consent WHERE study_session_id=?",
                (study_id,)).fetchone()
            if existing and existing[0] == version:
                return
            raise StudyError("试用已同意，不能替换协议版本")
        if state != "created":
            raise StudyError(f"状态 {state} 不允许同意")
        now = self._now()
        digest = hashlib.sha256(
            f"{row['protocol_version']}:{version}".encode("utf-8")).hexdigest()
        self._conn.execute(
            "UPDATE study_sessions SET status='consented', consented_at=? WHERE study_session_id=?",
            (now, study_id))
        self._conn.execute(
            "INSERT INTO study_consent"
            " (study_session_id, consent_version, consented_at, consent_hash) VALUES (?,?,?,?)",
            (study_id, version, now, digest))
        self._conn.commit()

    def is_recordable(self, study_session_id: str) -> bool:
        row = self._session(self._id(study_session_id))
        return bool(row and row["status"] in RECORDABLE_STATES)

    def status(self, study_session_id: str) -> Dict[str, Any]:
        row = self._session(self._id(study_session_id))
        if row is None:
            raise StudyError("study_session_id 不存在")
        return dict(row)

    def record(self, study_session_id: str, event_type: str,
               payload: Dict[str, Any], event_key: Optional[str] = None) -> int:
        study_id = self._id(study_session_id)
        event = self._text(event_type, "event_type", 80)
        if event not in EVENT_FIELDS:
            raise StudyError(f"不允许的试用事件: {event}")
        row = self._session(study_id)
        if row is None:
            raise StudyError("study_session_id 不存在")
        if row["status"] not in RECORDABLE_STATES:
            raise StudyNotRecordable(f"状态 {row['status']} 不允许写入试用事件")
        if not isinstance(payload, dict):
            raise StudyError("试用事件 payload 必须是对象")
        clean = self._validate_payload(event, payload)
        key = event_key or payload.get("event_key")
        if key is None:
            key = secrets.token_hex(12)
        key = self._text(key, "event_key", 160)
        if not _EVENT_KEY_RE.fullmatch(key):
            raise StudyError("event_key 含非法字符")
        now = self._now()
        try:
            cur = self._conn.execute(
                "INSERT INTO study_events"
                " (study_session_id, event_key, event_type, payload_json, created_at)"
                " VALUES (?,?,?,?,?)",
                (study_id, key, event, json.dumps(clean, ensure_ascii=False, sort_keys=True), now))
        except sqlite3.IntegrityError:
            existing = self._conn.execute(
                "SELECT id FROM study_events WHERE study_session_id=? AND event_key=?",
                (study_id, key)).fetchone()
            if existing is None:
                raise
            return int(existing[0])
        if row["status"] == "consented":
            self._conn.execute(
                "UPDATE study_sessions SET status='active', started_at=? WHERE study_session_id=?",
                (now, study_id))
        self._conn.commit()
        return int(cur.lastrowid)

    def complete(self, study_session_id: str, reason: str = "") -> None:
        study_id = self._id(study_session_id)
        row = self._session(study_id)
        if row is None:
            raise StudyError("study_session_id 不存在")
        if row["status"] == "completed":
            return
        if row["status"] not in {"consented", "active"}:
            raise StudyError(f"状态 {row['status']} 不允许结束")
        now = self._now()
        self._conn.execute(
            "UPDATE study_sessions SET status='completed', completed_at=?, completion_reason=?"
            " WHERE study_session_id=?",
            (now, str(reason or "")[:160], study_id))
        self._conn.commit()

    def withdraw(self, study_session_id: str) -> Dict[str, Any]:
        """标记撤回并立即清除事件载荷；会话元数据只保留删除所需的状态记录。"""
        study_id = self._id(study_session_id)
        row = self._session(study_id)
        if row is None:
            raise StudyError("study_session_id 不存在")
        if row["status"] == "withdrawn":
            return {"study_session_id": study_id, "status": "withdrawn", "deleted_events": 0}
        count = int(self._conn.execute(
            "SELECT COUNT(*) FROM study_events WHERE study_session_id=?", (study_id,)
        ).fetchone()[0])
        now = self._now()
        self._conn.execute("DELETE FROM study_events WHERE study_session_id=?", (study_id,))
        self._conn.execute(
            "UPDATE study_sessions SET status='withdrawn', withdrawn_at=? WHERE study_session_id=?",
            (now, study_id))
        self._conn.commit()
        return {"study_session_id": study_id, "status": "withdrawn", "deleted_events": count}

    def delete(self, study_session_id: str) -> Dict[str, Any]:
        study_id = self._id(study_session_id)
        row = self._session(study_id)
        if row is None:
            return {"study_session_id": study_id, "deleted": False, "deleted_events": 0}
        count = int(self._conn.execute(
            "SELECT COUNT(*) FROM study_events WHERE study_session_id=?", (study_id,)
        ).fetchone()[0])
        self._conn.execute("DELETE FROM study_sessions WHERE study_session_id=?", (study_id,))
        self._conn.commit()
        return {"study_session_id": study_id, "deleted": True, "deleted_events": count}

    def export_summary(self, study_session_id: Optional[str] = None) -> Dict[str, Any]:
        params: tuple[Any, ...] = ()
        where = ""
        if study_session_id:
            where = " WHERE s.study_session_id=?"
            params = (self._id(study_session_id),)
        rows = self._conn.execute(
            "SELECT s.study_session_id, s.protocol_version, s.scene_type, s.status,"
            " s.created_at, s.consented_at, s.started_at, s.completed_at, s.withdrawn_at,"
            " s.completion_reason,"
            " s.retention_until, e.event_type, e.created_at AS event_at"
            " FROM study_sessions s LEFT JOIN study_events e"
            " ON e.study_session_id=s.study_session_id" + where +
            " ORDER BY s.created_at, e.id", params).fetchall()
        grouped: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            sid = row["study_session_id"]
            item = grouped.setdefault(sid, {
                "study_session_id": sid,
                "protocol_version": row["protocol_version"],
                "scene_type": row["scene_type"],
                "status": row["status"],
                "created_at": int(row["created_at"]),
                "consented_at": row["consented_at"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "completion_reason": row["completion_reason"] or "",
                "withdrawn_at": row["withdrawn_at"],
                "retention_until": int(row["retention_until"]),
                "event_count": 0,
                "event_types": {},
            })
            if row["event_type"]:
                item["event_count"] += 1
                typ = row["event_type"]
                item["event_types"][typ] = item["event_types"].get(typ, 0) + 1
        result = list(grouped.values())
        return {"protocol": PROTOCOL_VERSION, "count": len(result),
                "sessions": result if study_session_id is None else result[:1]}

    def list_expired(self, now: Optional[int] = None) -> list[dict]:
        """列出保留期限已过的试用会话，不执行删除。"""
        cutoff = self._now() if now is None else int(now)
        rows = self._conn.execute(
            "SELECT study_session_id, status, retention_until "
            "FROM study_sessions WHERE retention_until < ? "
            "ORDER BY retention_until ASC, study_session_id ASC",
            (cutoff,),
        ).fetchall()
        return [{
            "study_session_id": row["study_session_id"],
            "status": row["status"],
            "retention_until": int(row["retention_until"]),
        } for row in rows]

    def _session(self, study_id: str):
        return self._conn.execute(
            "SELECT * FROM study_sessions WHERE study_session_id=?", (study_id,)
        ).fetchone()

    @staticmethod
    def _text(value: Any, field: str, limit: int) -> str:
        text = str(value or "").strip()
        if not text or len(text) > limit:
            raise StudyError(f"{field} 不能为空且长度不能超过 {limit}")
        return text

    @classmethod
    def _id(cls, value: Any) -> str:
        study_id = cls._text(value, "study_session_id", 80)
        if not _EVENT_KEY_RE.fullmatch(study_id):
            raise StudyError("study_session_id 含非法字符")
        return study_id

    @classmethod
    def _validate_payload(cls, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        allowed = EVENT_FIELDS[event_type]
        unknown = set(payload) - allowed - {"event_key"}
        if unknown:
            raise StudyError(f"试用事件包含未允许字段: {sorted(unknown)}")
        clean = {key: payload[key] for key in payload if key != "event_key"}
        encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True)
        if len(encoded) > 8192:
            raise StudyError("试用事件过大")
        # 结构化字段的最小类型检查，避免把任意原始对象伪装成事件。
        for key in ("conversation_ref", "scene_type", "level", "reason", "verdict", "source", "status", "stage", "kp_id", "target_source"):
            if key in clean and (not isinstance(clean[key], str) or len(clean[key]) > 160):
                raise StudyError(f"试用事件字段 {key} 类型或长度无效")
        if "kp_id" in clean and len(clean["kp_id"]) > 120:
            raise StudyError("试用事件字段 kp_id 长度无效")
        for key in ("text_chars", "error_count", "uncertain_count", "strategy_count", "covered_points", "total_points", "retell_pass_count", "notice_count", "queue_count", "steps", "degraded_count"):
            if key in clean and (type(clean[key]) is not int or clean[key] < 0):
                raise StudyError(f"试用事件字段 {key} 必须是非负整数")
        if "verdict" in clean and clean["verdict"] not in {"pass", "partial", "fail"}:
            raise StudyError("试用事件字段 verdict 必须是 pass / partial / fail")
        if "target_source" in clean and clean["target_source"] not in {"same_round", "previous_round", "none"}:
            raise StudyError("试用事件字段 target_source 无效")
        for key in ("degraded", "fallback", "scanned"):
            if key in clean and not isinstance(clean[key], bool):
                raise StudyError(f"试用事件字段 {key} 必须是 bool")
        if "strategies" in clean:
            strategies = clean["strategies"]
            if (not isinstance(strategies, list) or len(strategies) > 10
                    or not all(isinstance(item, dict) for item in strategies)):
                raise StudyError("strategies 必须是最多 10 项的对象列表")
            for item in strategies:
                if set(item) - {"mode", "hint_level", "reason_code", "kp_id"}:
                    raise StudyError("strategies 包含未允许字段")
        return clean


def _main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="A1 单机试用汇总与删除")
    parser.add_argument("command", choices=("create", "consent", "complete", "summary", "withdraw", "delete", "expired"))
    parser.add_argument("study_session_id", nargs="?")
    parser.add_argument("--consent-version", default="")
    parser.add_argument("--protocol-version", default=PROTOCOL_VERSION)
    parser.add_argument("--scene-type", default="general")
    parser.add_argument("--reason", default="")
    parser.add_argument("--root", default="data")
    args = parser.parse_args(list(argv) if argv is not None else None)
    tracker = StudyTracker(root=args.root)
    if args.command == "create":
        study_id = tracker.create_session(args.protocol_version, args.scene_type)
        print(json.dumps({"study_session_id": study_id, "status": "created"},
                         ensure_ascii=False, indent=2))
        return 0
    if args.command == "consent":
        if not args.study_session_id or not args.consent_version:
            parser.error("consent 需要 study_session_id 和 --consent-version")
        tracker.consent(args.study_session_id, args.consent_version)
        print(json.dumps({"study_session_id": args.study_session_id,
                          "status": "consented"}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "complete":
        if not args.study_session_id:
            parser.error("complete 需要 study_session_id")
        tracker.complete(args.study_session_id, args.reason)
        print(json.dumps({"study_session_id": args.study_session_id,
                          "status": "completed"}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "summary":
        print(json.dumps(tracker.export_summary(args.study_session_id), ensure_ascii=False, indent=2))
        return 0
    if args.command == "expired":
        now = tracker._now()
        sessions = tracker.list_expired(now=now)
        print(json.dumps({"now": now, "count": len(sessions), "sessions": sessions},
                         ensure_ascii=False, indent=2))
        return 0
    if args.command == "withdraw":
        if not args.study_session_id:
            parser.error("withdraw 需要 study_session_id")
        print(json.dumps(tracker.withdraw(args.study_session_id),
                         ensure_ascii=False, indent=2))
        return 0
    if not args.study_session_id:
        parser.error("delete 需要 study_session_id")
    print(json.dumps(tracker.delete(args.study_session_id), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

# ============================================================
# engine/memory/store.py
# 共享 SQLite 存储层（0.31 主流化第 3 步）
# 单库 data/coach.db 承载两类"行记录型"数据：
#   - ledger_events ：append-only 错误事件账本（原 ledger_<learner>.json）
#   - sessions/messages/profiles ：会话记忆（原 memory_<learner>.json）
# graph 仍走"内存对象 + JSON 快照"（算法全在内存，SQLite 无收益，业界共识）；
# providers/quota/feedback 等小状态文件仍走 JSON。
# 线程模型：check_same_thread=False（ThreadingHTTPServer 每请求一线程），
#   写路径由 dialog_service 的 RLock 串行化；跨连接并发安全靠 SQLite
#   自身锁 + busy_timeout + WAL。
# 遗留迁移：首次 ensure_store 时把 ledger_*.json / memory_*.json 导入后
#   改名 .json.migrated（保留原文件作备份，绝不删除用户数据）；导入完成
#   记录在 migrations 表，幂等可重入。
# ============================================================

import glob
import json
import os
import sqlite3
import time
from typing import Any, Dict

DB_NAME = "coach.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  learner_id  TEXT NOT NULL,
  kind        TEXT NOT NULL,
  kp_id       TEXT NOT NULL,
  signature   TEXT,
  evidence    TEXT,
  ts          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_learner_kp ON ledger_events(learner_id, kp_id);
CREATE INDEX IF NOT EXISTS idx_ledger_learner_id ON ledger_events(learner_id, id);

CREATE TABLE IF NOT EXISTS sessions (
  learner_id  TEXT NOT NULL,
  session_id  TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  title       TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'active',
  pinned      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (learner_id, session_id)
);

CREATE TABLE IF NOT EXISTS messages (
  learner_id    TEXT NOT NULL,
  session_id    TEXT NOT NULL,
  seq           INTEGER NOT NULL,
  role          TEXT NOT NULL,
  content       TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  ts            INTEGER NOT NULL,
  PRIMARY KEY (learner_id, session_id, seq)
);

CREATE TABLE IF NOT EXISTS profiles (
  learner_id   TEXT PRIMARY KEY,
  profile_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS migrations (
  path TEXT PRIMARY KEY,
  ts   INTEGER NOT NULL
);
"""


def connect(root: str = "data") -> sqlite3.Connection:
    """打开（或创建）coach.db 并建好 schema，返回连接。
    check_same_thread=False：serve 线程模型需要；写串行化由上层 RLock 保证。"""
    os.makedirs(root, exist_ok=True)
    conn = sqlite3.connect(os.path.join(root, DB_NAME),
                           timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


def ensure_store(root: str = "data") -> sqlite3.Connection:
    """connect + 遗留 JSON 一次性迁移（幂等）。所有使用方入口统一走这里。"""
    conn = connect(root)
    _migrate_legacy(root, conn)
    return conn


# ---------------- 遗留 JSON 迁移 ----------------

def _read_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return d if isinstance(d, dict) else {}


def _migrated(conn: sqlite3.Connection, path: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM migrations WHERE path=?", (path,)).fetchone() is not None


def _mark_migrated(conn: sqlite3.Connection, path: str) -> None:
    conn.execute("INSERT OR REPLACE INTO migrations VALUES (?, ?)",
                 (path, int(time.time())))


def _migrate_legacy(root: str, conn: sqlite3.Connection) -> None:
    for fp in sorted(glob.glob(os.path.join(root, "ledger_*.json"))):
        if _migrated(conn, fp):
            continue
        name = os.path.basename(fp)[len("ledger_"):-len(".json")]
        try:
            data = _read_json(fp)
        except (OSError, json.JSONDecodeError):
            continue          # 损坏文件：跳过不迁移，保留原状待人工排查
        learner = str(data.get("learner_id") or name)
        for e in data.get("events") or []:
            kp = str(e.get("kp_id") or "")
            conn.execute(
                "INSERT INTO ledger_events"
                " (learner_id, kind, kp_id, signature, evidence, ts)"
                " VALUES (?,?,?,?,?,?)",
                (learner, str(e.get("kind") or ""), kp,
                 str(e.get("signature") or kp),
                 str(e.get("evidence") or "")[:200],
                 int(e.get("ts") or 0)))
        conn.commit()
        _mark_migrated(conn, fp)
        os.replace(fp, fp + ".migrated")

    for fp in sorted(glob.glob(os.path.join(root, "memory_*.json"))):
        if _migrated(conn, fp):
            continue
        name = os.path.basename(fp)[len("memory_"):-len(".json")]
        try:
            data = _read_json(fp)
        except (OSError, json.JSONDecodeError):
            continue
        learner = str(data.get("learner_id") or name)
        now = int(time.time())
        sessions = data.get("sessions") or {}
        if isinstance(sessions, dict):
            for sid, sess in sessions.items():
                if not isinstance(sess, dict):
                    continue
                ca = int(sess.get("created_at") or now)
                ua = int(sess.get("updated_at") or ca)
                conn.execute(
                    "INSERT OR IGNORE INTO sessions"
                    " (learner_id, session_id, created_at, updated_at,"
                    "  title, status, pinned) VALUES (?,?,?,?,?,?,?)",
                    (learner, sid, ca, ua,
                     str(sess.get("title") or ""),
                     str(sess.get("status") or "active"),
                     1 if sess.get("pinned") else 0))
                for i, m in enumerate(sess.get("messages") or [], start=1):
                    try:
                        seq = int(str(m.get("id") or "")[1:]) or i
                    except ValueError:
                        seq = i
                    conn.execute(
                        "INSERT OR IGNORE INTO messages"
                        " (learner_id, session_id, seq, role, content,"
                        "  metadata_json, ts) VALUES (?,?,?,?,?,?,?)",
                        (learner, sid, seq,
                         str(m.get("role") or "user"),
                         str(m.get("content") or ""),
                         json.dumps(m.get("metadata") or {},
                                    ensure_ascii=False),
                         ua))
        profile = data.get("profile") or {}
        if profile:
            conn.execute(
                "INSERT OR REPLACE INTO profiles VALUES (?, ?)",
                (learner, json.dumps(profile, ensure_ascii=False)))
        conn.commit()
        _mark_migrated(conn, fp)
        os.replace(fp, fp + ".migrated")

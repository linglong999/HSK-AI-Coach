# ============================================================
# engine/memory/learner_memory.py
# M5 多轮记忆 · 按 learner 隔离的对话历史持久化
# 借鉴 OpenMAIC：
#   - append-only messages + 会话状态快照（chat_storage-core.ts 双记录）
#   - 注入规范化，只透 user/assistant（personal-history-tools visibleChatMessages）
#   - 确定性摘要最近 N 条×单条上限（summarizers/conversation-summary.ts）
# 0.31 主流化第 3 步：JSON 文件 → SQLite（data/coach.db 的 sessions/
#   messages/profiles 三表，对齐 LangChain SQLChatMessageHistory 一类的主流做法）；
#   profile 为嵌套画像 → JSON 列（SQLite 官方支持的混合模式）。
# ============================================================

import json
import re
import time
from typing import Any, Dict, List, Optional

from engine.memory import store

ALLOWED_ROLES = {"user", "assistant"}


class LearnerMemory:
    """按 learner_id 隔离的多轮对话记忆（coach.db 三表）。"""

    DEFAULT_WINDOW = 20        # 注入窗口上限
    SUMMARY_N = 10             # 摘要最近条数
    SUMMARY_MAX_CHARS = 200    # 摘要单条字符上限
    PERSIST_WINDOW = 200       # 持久化裁剪窗口

    def __init__(self, learner_id: str = "default", root: str = "data"):
        self.learner_id = learner_id
        self.root = root
        self._conn = store.ensure_store(root)

    def _safe(self, name: str) -> str:
        """learner_id / session_id 安全化（防注入与路径穿越）。"""
        return re.sub(r"[^A-Za-z0-9_\-]", "_", name)

    # ---------------- 写 ----------------
    def append(self, session_id: str, role: str, content: str,
               metadata: Optional[Dict[str, Any]] = None) -> str:
        """追加一条消息，返回 msg id。role 非法抛 ValueError。"""
        if role not in ALLOWED_ROLES:
            raise ValueError(f"非法 role: {role}（仅允许 {sorted(ALLOWED_ROLES)}）")
        sid = self._safe(session_id)
        ts = int(time.time())
        self._conn.execute(
            "INSERT OR IGNORE INTO sessions"
            " (learner_id, session_id, created_at, updated_at)"
            " VALUES (?,?,?,?)",
            (self.learner_id, sid, ts, ts))
        seq = int(self._conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM messages"
            " WHERE learner_id=? AND session_id=?",
            (self.learner_id, sid)).fetchone()[0])
        self._conn.execute(
            "INSERT INTO messages"
            " (learner_id, session_id, seq, role, content, metadata_json, ts)"
            " VALUES (?,?,?,?,?,?,?)",
            (self.learner_id, sid, seq, role, str(content),
             json.dumps(dict(metadata or {}), ensure_ascii=False), ts))
        self._conn.execute(
            "UPDATE sessions SET updated_at=?"
            " WHERE learner_id=? AND session_id=?",
            (ts, self.learner_id, sid))
        # 持久化裁剪窗口（对齐 MAX_MESSAGES_PER_SESSION）
        self._conn.execute(
            "DELETE FROM messages WHERE learner_id=? AND session_id=?"
            " AND seq <= (SELECT MAX(seq)-? FROM messages"
            "             WHERE learner_id=? AND session_id=?)",
            (self.learner_id, sid, self.PERSIST_WINDOW,
             self.learner_id, sid))
        self._conn.commit()
        return f"m{seq}"

    def touch(self, session_id: str, title: Optional[str] = None,
              status: Optional[str] = None, pinned: Optional[bool] = None,
              bump: bool = True) -> None:
        """更新会话元信息（title/status/pinned/updated_at），不追加消息。
        bump=False：置顶/重命名等管理操作不改变 updated_at（排序只反映对话活跃度）。"""
        sid = self._safe(session_id)
        now = int(time.time())
        self._conn.execute(
            "INSERT OR IGNORE INTO sessions"
            " (learner_id, session_id, created_at, updated_at)"
            " VALUES (?,?,?,?)",
            (self.learner_id, sid, now, now))
        sets, args = [], []
        if title is not None:
            sets.append("title=?"); args.append(str(title))
        if status is not None:
            sets.append("status=?"); args.append(str(status))
        if pinned is not None:
            sets.append("pinned=?"); args.append(1 if pinned else 0)
        if bump:
            sets.append("updated_at=?"); args.append(now)
        if sets:
            args.extend([self.learner_id, sid])
            self._conn.execute(
                f"UPDATE sessions SET {', '.join(sets)}"  # noqa: S608 白名单字段
                " WHERE learner_id=? AND session_id=?", args)
        self._conn.commit()

    def delete_session(self, session_id: str) -> bool:
        """删除整个会话（消息+元信息）。不存在返回 False。"""
        sid = self._safe(session_id)
        exists = self._conn.execute(
            "SELECT 1 FROM sessions WHERE learner_id=? AND session_id=?",
            (self.learner_id, sid)).fetchone()
        if exists is None:
            return False
        self._conn.execute(
            "DELETE FROM messages WHERE learner_id=? AND session_id=?",
            (self.learner_id, sid))
        self._conn.execute(
            "DELETE FROM sessions WHERE learner_id=? AND session_id=?",
            (self.learner_id, sid))
        self._conn.commit()
        return True

    # ---------------- 读（LLM 注入前） ----------------
    def get_history(self, session_id: str,
                    window: int = DEFAULT_WINDOW) -> List[Dict[str, Any]]:
        """取最近 window 条原始消息（含 metadata）。"""
        sid = self._safe(session_id)
        rows = self._conn.execute(
            "SELECT seq, role, content, metadata_json FROM messages"
            " WHERE learner_id=? AND session_id=?"
            " ORDER BY seq DESC LIMIT ?",
            (self.learner_id, sid, max(0, window))).fetchall()
        return [{
            "id": f"m{r['seq']}",
            "role": r["role"],
            "content": r["content"],
            "metadata": json.loads(r["metadata_json"] or "{}"),
        } for r in reversed(rows)]

    def to_llm_history(self, session_id: str,
                       window: int = DEFAULT_WINDOW) -> List[Dict[str, str]]:
        """规范化输入：只透 user/assistant 的 {role, content}（对齐 visibleChatMessages），
        丢弃 system/raw tool payload，保持注入纯净。"""
        out = []
        for msg in self.get_history(session_id, window=window):
            if msg["role"] not in ALLOWED_ROLES:
                continue
            c = msg["content"].strip()
            if not c:
                continue
            out.append({"role": msg["role"], "content": c})
        return out

    def get_summary(self, session_id: str, n: int = SUMMARY_N,
                    max_chars: int = SUMMARY_MAX_CHARS) -> str:
        """确定性摘要：最近 n 条 × 单条 max_chars 截断（仿 summarizeConversation）。
        可复现、无 LLM。"""
        sid = self._safe(session_id)
        rows = self._conn.execute(
            "SELECT role, content FROM messages"
            " WHERE learner_id=? AND session_id=?"
            " ORDER BY seq DESC LIMIT ?",
            (self.learner_id, sid, max(0, n))).fetchall()
        lines = []
        for r in reversed(rows):
            if r["role"] not in ALLOWED_ROLES:
                continue
            role_label = "用户" if r["role"] == "user" else "助教"
            content = r["content"].replace("\n", " ")
            if len(content) > max_chars:
                content = content[:max_chars] + "…"
            lines.append(f"[{role_label}] {content}")
        return "\n".join(lines)

    # ---------------- 画像 ----------------
    def get_profile(self) -> Dict[str, Any]:
        row = self._conn.execute(
            "SELECT profile_json FROM profiles WHERE learner_id=?",
            (self.learner_id,)).fetchone()
        if row is None:
            return {}
        try:
            d = json.loads(row[0] or "{}")
            return d if isinstance(d, dict) else {}
        except json.JSONDecodeError:
            return {}

    def update_profile(self, **fields) -> None:
        prof = self.get_profile()
        prof.update(fields)
        self._conn.execute(
            "INSERT OR REPLACE INTO profiles VALUES (?, ?)",
            (self.learner_id, json.dumps(prof, ensure_ascii=False)))
        self._conn.commit()

    # ---------------- 检索 ----------------
    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """按 content 包含 query 跨会话搜索，返回可见消息（含 session_id），
        不暴露 metadata 敏感内部项。"""
        if not query:
            return []
        rows = self._conn.execute(
            "SELECT session_id, seq, role, content FROM messages"
            " WHERE learner_id=? AND role IN ('user','assistant')"
            "   AND instr(content, ?) > 0"
            " ORDER BY rowid LIMIT ?",
            (self.learner_id, query, max(0, limit))).fetchall()
        return [{
            "session_id": r["session_id"],
            "id": f"m{r['seq']}",
            "role": r["role"],
            "content": r["content"],
        } for r in rows]

    # ---------------- 会话列表（serve /api/profile 数据源） ----------------
    def has_session(self, session_id: str) -> bool:
        sid = self._safe(session_id)
        return self._conn.execute(
            "SELECT 1 FROM sessions WHERE learner_id=? AND session_id=?",
            (self.learner_id, sid)).fetchone() is not None

    def list_sessions(self) -> List[Dict[str, Any]]:
        """该 learner 全部会话元信息（不含 messages，防载荷膨胀）。"""
        rows = self._conn.execute(
            "SELECT s.session_id, s.title, s.status, s.pinned,"
            "       s.created_at, s.updated_at,"
            "       (SELECT COUNT(*) FROM messages m"
            "         WHERE m.learner_id=s.learner_id"
            "           AND m.session_id=s.session_id) AS message_count"
            " FROM sessions s WHERE s.learner_id=?",
            (self.learner_id,)).fetchall()
        return [{
            "id": r["session_id"],
            "title": r["title"] or "",
            "status": r["status"] or "active",
            "updated_at": int(r["updated_at"] or 0),
            "message_count": int(r["message_count"] or 0),
            "pinned": bool(r["pinned"]),
        } for r in rows]

    # ---------------- 内部 ----------------
    def reload(self) -> None:
        """重开连接（测试/多进程用；SQLite 每查询即最新，兼容既有调用点）。"""
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass
        self._conn = store.ensure_store(self.root)

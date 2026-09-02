# ============================================================
# engine/memory/learner_memory.py
# M5 多轮记忆 · 按 learner 隔离的对话历史持久化
# 借鉴 OpenMAIC：
#   - append-only messages + 会话状态快照（chat_storage-core.ts 双记录）
#   - 注入规范化，只透 user/assistant（personal-history-tools visibleChatMessages）
#   - 确定性摘要最近 N 条×单条上限（summarizers/conversation-summary.ts）
# 落地约束：零第三方依赖；JSON 文件 + tmp+rename 原子写（对齐 error_graph.py）
# ============================================================

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

ALLOWED_ROLES = {"user", "assistant"}


class LearnerMemory:
    """按 learner_id 隔离的多轮对话记忆。每个 learner 一个 JSON 文件。"""

    DEFAULT_WINDOW = 20        # 注入窗口上限
    SUMMARY_N = 10             # 摘要最近条数
    SUMMARY_MAX_CHARS = 200    # 摘要单条字符上限
    PERSIST_WINDOW = 200       # 持久化裁剪窗口

    def __init__(self, learner_id: str = "default", root: str = "data"):
        self.learner_id = learner_id
        self.root = root
        self._path = os.path.join(root, f"memory_{self._safe(learner_id)}.json")
        self._data: Dict[str, Any] = None
        self._msg_seq: Dict[str, int] = {}   # session -> 下一条 msg index

    def _safe(self, name: str) -> str:
        """learner_id / session_id 安全化（防路径穿越）。"""
        return re.sub(r"[^A-Za-z0-9_\-]", "_", name)

    # ---------------- 写 ----------------
    def append(self, session_id: str, role: str, content: str,
               metadata: Optional[Dict[str, Any]] = None) -> str:
        """追加一条消息，返回 msg id。role 非法抛 ValueError。"""
        if role not in ALLOWED_ROLES:
            raise ValueError(f"非法 role: {role}（仅允许 {sorted(ALLOWED_ROLES)}）")
        sid = self._safe(session_id)
        data = self._ensure(sid)
        sess = data["sessions"][sid]

        seq = self._msg_seq.get(sid, 0) + 1
        self._msg_seq[sid] = seq
        msg_id = f"m{seq}"
        ts = int(time.time())
        sess["messages"].append({
            "id": msg_id,
            "role": role,
            "content": str(content),
            "metadata": dict(metadata or {}),
        })
        self._refresh_meta(sess, data, ts)

        # 持久化裁剪窗口（对齐 MAX_MESSAGES_PER_SESSION）
        sess["messages"] = sess["messages"][-self.PERSIST_WINDOW:]
        self._refresh_meta(sess, data, ts, force=True)
        self._save()
        return msg_id

    def touch(self, session_id: str, title: Optional[str] = None,
              status: Optional[str] = None) -> None:
        """更新会话元信息（title/status/updated_at），不追加消息。"""
        sid = self._safe(session_id)
        data = self._ensure(sid)
        sess = data["sessions"][sid]
        if title is not None:
            sess["title"] = title
        if status is not None:
            sess["status"] = status
        sess["updated_at"] = int(time.time())
        self._save()

    # ---------------- 读（LLM 注入前） ----------------
    def get_history(self, session_id: str, window: int = DEFAULT_WINDOW) -> List[Dict[str, Any]]:
        """取最近 window 条原始消息（含 metadata）。"""
        sid = self._safe(session_id)
        sess = self._get(sid)
        if sess is None:
            return []
        return sess["messages"][-window:]

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
        sess = self._get(self._safe(session_id))
        if sess is None:
            return ""
        recent = sess["messages"][-n:]
        lines = []
        for msg in recent:
            role_label = "用户" if msg["role"] == "user" else "助教"
            content = msg["content"].replace("\n", " ")
            if len(content) > max_chars:
                content = content[:max_chars] + "…"
            lines.append(f"[{role_label}] {content}")
        return "\n".join(lines)

    # ---------------- 画像 ----------------
    def get_profile(self) -> Dict[str, Any]:
        data = self._load()
        return dict(data.get("profile", {}))

    def update_profile(self, **fields) -> None:
        data = self._load()
        prof = data.setdefault("profile", {})
        for k, v in fields.items():
            prof[k] = v
        self._save()

    # ---------------- 检索 ----------------
    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """按 content 包含 query 跨会话搜索，返回可见消息（含 session_id），
        不暴露 metadata 敏感内部项。"""
        if not query:
            return []
        data = self._load()
        hits = []
        for sid, sess in data.get("sessions", {}).items():
            for msg in sess["messages"]:
                if msg["role"] not in ALLOWED_ROLES:
                    continue
                if query in msg["content"]:
                    hits.append({
                        "session_id": sid,
                        "id": msg["id"],
                        "role": msg["role"],
                        "content": msg["content"],
                    })
                    if len(hits) >= limit:
                        return hits
        return hits

    # ---------------- 内部 ----------------
    def _ensure(self, session_id: str) -> Dict[str, Any]:
        """保证 session 存在，返回 data。"""
        sid = self._safe(session_id)
        data = self._load()
        if sid not in data["sessions"]:
            data["sessions"][sid] = {
                "id": sid, "created_at": int(time.time()),
                "updated_at": int(time.time()), "title": "",
                "status": "active", "message_ids": [], "messages": [],
            }
        return data

    def _refresh_meta(self, sess: Dict[str, Any], data: Dict[str, Any],
                      ts: int, force: bool = False) -> None:
        """维护 message_ids 快照（仿 chat_session_state.messageIds）。"""
        sess["updated_at"] = ts
        sess["message_ids"] = [m["id"] for m in sess["messages"]]

    def _get(self, session_id: str) -> Optional[Dict[str, Any]]:
        data = self._load()
        return data["sessions"].get(session_id)

    def _load(self) -> Dict[str, Any]:
        if self._data is not None:
            return self._data
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as f:
                    raw = json.load(f)
                self._data = {
                    "learner_id": raw.get("learner_id", self.learner_id),
                    "profile": raw.get("profile", {}),
                    "sessions": raw.get("sessions", {}),
                }
                # 恢复每 session 的 seq（下一条 id 基于最后一条）
                for sid, sess in self._data["sessions"].items():
                    ids = [m["id"] for m in sess.get("messages", [])]
                    self._msg_seq[sid] = self._max_seq(ids)
                return self._data
            except (json.JSONDecodeError, OSError) as e:
                # 损坏文件：defensive 用一个新空骨架，不崩（宁漏勿错）
                self._data = self._fresh()
                return self._data
        self._data = self._fresh()
        return self._data

    def _fresh(self) -> Dict[str, Any]:
        return {"learner_id": self.learner_id, "profile": {}, "sessions": {}}

    def _max_seq(self, ids: List[str]) -> int:
        maxv = 0
        for i in ids:
            try:
                maxv = max(maxv, int(i[1:]))
            except (ValueError, IndexError):
                pass
        return maxv

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)   # 原子写，无 .tmp 残留

    def reload(self) -> None:
        """强制从磁盘重读（测试/多进程用）。"""
        self._data = None
        self._msg_seq.clear()
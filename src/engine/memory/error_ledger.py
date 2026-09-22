# ============================================================
# engine/memory/error_ledger.py
# M8 · 通用 append-only 错误事件账本（决策 A-乙）
# 借鉴 OpenMAIC engagementEvent ledger + ring buffer 截断（fold.ts）
# 0.31 主流化第 3 步：JSON 文件 → SQLite（data/coach.db · ledger_events 表）
#   行记录型数据（追加/按 kp 聚合/取最近 N）是 SQLite 的主场；
#   语义与 JSON 版逐条对齐（repeated_error 判定、惯犯阈值、ring buffer）。
# ============================================================

import time
from typing import Any, Dict, List, Optional

from engine.memory import store

KINDS = {"observation_error", "repeated_error",
         "concept_confirmed", "concept_reviewed"}
MAX_EVENTS = 500          # ring buffer 上限（仿 MAX_ENGAGEMENT_EVENTS）
REPEAT_THRESHOLD = 3      # 惯犯判定阈值（决策 B-乙）


class ErrorLedger:
    """按 learner 隔离的错误事件账本（coach.db / ledger_events 表）。
    事件 id 由 SQLite 自增生成（ev<n>），ring buffer 截断后仍单调不重复。"""

    def __init__(self, learner_id: str = "default", root: str = "data"):
        self.learner_id = learner_id
        self._root = root
        self._conn = store.ensure_store(root)

    # ---------------- 写 ----------------
    def record(self, kind: str, kp_id: str, signature: Optional[str] = None,
               evidence: str = "") -> str:
        """追加一条事件，返回 event_id。
        同一 kp_id+signature 已存 → 记为 repeated_error 且累加该 KP 计数。"""
        if kind not in KINDS:
            raise ValueError(f"kind 非法: {kind}（允许 {sorted(KINDS)}）")
        ts = int(time.time())
        sig = signature or kp_id

        # 同 kp+signature 再现 → repeated_error（仅观察类；confirm/review 再现
        # 仍按原 kind 记，否则二次确认会被误标 repeated_error 污染跨轮推导）
        prev = self._conn.execute(
            "SELECT 1 FROM ledger_events"
            " WHERE learner_id=? AND kp_id=? AND signature=? LIMIT 1",
            (self.learner_id, kp_id, sig)).fetchone()
        eff_kind = "repeated_error" if (prev and kind == "observation_error") else kind

        cur = self._conn.execute(
            "INSERT INTO ledger_events"
            " (learner_id, kind, kp_id, signature, evidence, ts)"
            " VALUES (?,?,?,?,?,?)",
            (self.learner_id, eff_kind, kp_id, sig, str(evidence)[:200], ts))
        eid = f"ev{cur.lastrowid}"
        # ring buffer：仅保留该 learner 最近 MAX_EVENTS 条
        self._conn.execute(
            "DELETE FROM ledger_events WHERE learner_id=? AND id NOT IN ("
            "  SELECT id FROM ledger_events WHERE learner_id=?"
            "  ORDER BY id DESC LIMIT ?)",
            (self.learner_id, self.learner_id, MAX_EVENTS))
        self._conn.commit()
        return eid

    def confirm(self, kp_id: str, evidence: str = "") -> str:
        return self.record("concept_confirmed", kp_id, signature=kp_id, evidence=evidence)

    def review(self, kp_id: str, evidence: str = "") -> str:
        return self.record("concept_reviewed", kp_id, signature=kp_id, evidence=evidence)

    # ---------------- 读 ----------------
    def count_for_kp(self, kp_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM ledger_events"
            " WHERE learner_id=? AND kp_id=?",
            (self.learner_id, kp_id)).fetchone()
        return int(row[0])

    def repeated_count(self, kp_id: str) -> int:
        """该 KP 的累计"撞错"次数（observation_error/repeated_error）。
        确认/复习事件不计入——错 2 次+确认 1 次不构成惯犯（宁漏勿错）。"""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM ledger_events"
            " WHERE learner_id=? AND kp_id=?"
            "   AND kind IN ('observation_error','repeated_error')",
            (self.learner_id, kp_id)).fetchone()
        return int(row[0])

    def is_repeat_offender(self, kp_id: str, threshold: int = REPEAT_THRESHOLD) -> bool:
        """惯犯判定（决策 B-乙：>=3 才算，宁漏勿错）。"""
        return self.repeated_count(kp_id) >= max(1, threshold)

    def recent(self, window: int = 50) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM ledger_events WHERE learner_id=?"
            " ORDER BY id DESC LIMIT ?",
            (self.learner_id, max(0, window))).fetchall()
        return [self._to_event(r) for r in reversed(rows)]

    # ---------------- 内部 ----------------
    @staticmethod
    def _to_event(r) -> Dict[str, Any]:
        return {
            "id": f"ev{r['id']}",
            "kind": r["kind"],
            "learner_id": r["learner_id"],
            "kp_id": r["kp_id"],
            "signature": r["signature"],
            "evidence": r["evidence"] or "",
            "ts": r["ts"],
        }

    def reload(self) -> None:
        """重开连接（测试/多进程强制取新视图；SQLite 本身无内存缓存，
        每次查询即最新，此方法仅为兼容既有调用点）。"""
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass
        self._conn = store.ensure_store(self._root)

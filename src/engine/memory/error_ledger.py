# ============================================================
# engine/memory/error_ledger.py
# M8 · 通用 append-only 错误事件账本（决策 A-乙）
# 借鉴 OpenMAIC engagementEvent ledger + ring buffer 截断（fold.ts）
# 0.31 主流化第 3 步：JSON 文件 → SQLite（data/coach.db · ledger_events 表）
#   行记录型数据（追加/按 kp 聚合/取最近 N）是 SQLite 的主场；
#   语义与 JSON 版逐条对齐（repeated_error 判定、惯犯阈值、ring buffer）。
# ============================================================

import json
import time
from typing import Any, Dict, List, Optional

from engine.memory import store

KINDS = {"observation_error", "repeated_error",
         "concept_confirmed", "concept_reviewed",
         "feedback_presented", "uptake_observed",   # B2：反馈呈现/采纳观测
         "avoidance_observed"}                      # B3：回避观测（该用未用）
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

    def present_feedback(self, kp_id: str, signature: Optional[str] = None,
                         evidence: str = "") -> str:
        """B2：反馈呈现（deep/light 点呈现时记）。signature 建议带 fragment
        区分同 kp 不同错误点；复用 record() 幂等链，但不会被误改 repeated_error。"""
        return self.record("feedback_presented", kp_id,
                           signature=signature or kp_id, evidence=evidence)

    def observe_uptake(self, kp_id: str, evidence: str = "") -> str:
        """B2：采纳观测——feedback_presented 后同会话该 kp 修正被确认/产出无错。"""
        return self.record("uptake_observed", kp_id,
                           signature=kp_id, evidence=evidence)

    def observe_avoidance(self, kp_id: str, state: str,
                          evidence: str = "") -> str:
        """B3：回避观测——"该用未用"发生时记账（state ∈ 图谱四态线索）。
        与"错误"分型可查：avoidance_* 独立事件，走 record() 幂等链（不误改 repeated）。"""
        return self.record("avoidance_observed", kp_id,
                           signature=kp_id,
                           evidence=f"state={state} {evidence}".strip())

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


def query_meta_events(learner_id: str = "default", root: str = "data"):
    """B3 G2：从 messages.metadata_json 聚合元认知自评事件，返回成对的
    (self_level, outcome_at_time) 序列——供 B6 ECE/Brier/Calibration-Gap 直算。
    每条 meta_event=="self_rating" 且含合法 self_level/outcome_at_time 才纳入；
    锚点自由文本 / 字段残缺事件跳过（校验器语义）。库不可用时返回空列表（不炸）。"""
    pairs = []
    try:
        conn = store.ensure_store(root)
        rows = conn.execute(
            "SELECT metadata_json FROM messages WHERE learner_id=?"
            " ORDER BY ts ASC, seq ASC", (learner_id,)).fetchall()
    except Exception:  # noqa: BLE001 库不可用/SQL 异常 → 空，不阻断
        return pairs
    for (meta_json,) in rows:
        if not meta_json:
            continue
        try:
            meta = json.loads(meta_json) if isinstance(meta_json, str) else meta_json
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(meta, dict) or meta.get("meta_event") != "self_rating":
            continue
        lv = meta.get("self_level")
        out = meta.get("outcome_at_time")
        if lv in ("certain", "half", "uncertain") and out in ("correct", "wrong"):
            pairs.append((lv, out))
    return pairs

# ============================================================
# engine/memory/error_ledger.py
# M8 · 通用 append-only 错误事件账本（决策 A-乙）
# 借鉴 OpenMAIC engagementEvent ledger + ring buffer 截断（fold.ts）
# 落地：JSON + tmp+rename 原子写；同 kp+signature 再现 → repeated_error
# ============================================================

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

KINDS = {"observation_error", "repeated_error",
         "concept_confirmed", "concept_reviewed"}
MAX_EVENTS = 500          # ring buffer 上限（仿 MAX_ENGAGEMENT_EVENTS）
REPEAT_THRESHOLD = 3      # 惯犯判定阈值（决策 B-乙）


class ErrorLedger:
    """按 learner 隔离的错误事件账本。一个 JSON 文件。"""

    def __init__(self, learner_id: str = "default", root: str = "data"):
        self.learner_id = learner_id
        self._path = os.path.join(root, f"ledger_{self._safe(learner_id)}.json")
        self._data: Optional[Dict[str, Any]] = None

    def _safe(self, name: str) -> str:
        return re.sub(r"[^A-Za-z0-9_\-]", "_", name)

    # ---------------- 写 ----------------
    def record(self, kind: str, kp_id: str, signature: Optional[str] = None,
               evidence: str = "") -> str:
        """追加一条事件，返回 event_id。
        同一 kp_id+signature 已存 → 记为 repeated_error 且累加该 KP 计数。"""
        if kind not in KINDS:
            raise ValueError(f"kind 非法: {kind}（允许 {sorted(KINDS)}）")
        data = self._load()
        events = data["events"]
        ts = int(time.time())
        eid = f"ev{len(events)+1}"
        sig = signature or kp_id

        # 同 kp+signature 再现 → repeated_error（仅观察类；confirm/review 再现
        # 仍按原 kind 记，否则二次确认会被误标 repeated_error 污染跨轮推导）
        prev = [e for e in events
                if e["kp_id"] == kp_id and (e.get("signature") or e["kp_id"]) == sig]
        eff_kind = "repeated_error" if (prev and kind == "observation_error") else kind
        events.append({
            "id": eid, "kind": eff_kind, "learner_id": self.learner_id,
            "kp_id": kp_id, "signature": sig, "evidence": str(evidence)[:200],
            "ts": ts,
        })
        # ring buffer
        if len(events) > MAX_EVENTS:
            data["events"] = events[-MAX_EVENTS:]
        self._save()
        return eid

    def confirm(self, kp_id: str, evidence: str = "") -> str:
        return self.record("concept_confirmed", kp_id, signature=kp_id, evidence=evidence)

    def review(self, kp_id: str, evidence: str = "") -> str:
        return self.record("concept_reviewed", kp_id, signature=kp_id, evidence=evidence)

    # ---------------- 读 ----------------
    def count_for_kp(self, kp_id: str) -> int:
        return sum(1 for e in self._load()["events"] if e["kp_id"] == kp_id)

    def repeated_count(self, kp_id: str) -> int:
        """该 KP 的累计"撞错"次数（observation_error/repeated_error）。
        确认/复习事件不计入——错 2 次+确认 1 次不构成惯犯（宁漏勿错）。"""
        return sum(1 for e in self._load()["events"]
                   if e["kp_id"] == kp_id
                   and e.get("kind") in ("observation_error", "repeated_error"))

    def is_repeat_offender(self, kp_id: str, threshold: int = REPEAT_THRESHOLD) -> bool:
        """惯犯判定（决策 B-乙：>=3 才算，宁漏勿错）。"""
        return self.repeated_count(kp_id) >= max(1, threshold)

    def recent(self, window: int = 50) -> List[Dict[str, Any]]:
        return self._load()["events"][-window:]

    # ---------------- 内部 ----------------
    def _load(self) -> Dict[str, Any]:
        if self._data is not None:
            return self._data
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as f:
                    raw = json.load(f)
                self._data = {
                    "learner_id": raw.get("learner_id", self.learner_id),
                    "events": raw.get("events", []),
                }
                return self._data
            except (json.JSONDecodeError, OSError):
                self._data = self._fresh()
                return self._data
        self._data = self._fresh()
        return self._data

    def _fresh(self) -> Dict[str, Any]:
        return {"learner_id": self.learner_id, "events": []}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    def reload(self) -> None:
        self._data = None
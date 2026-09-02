# ============================================================
# engine/memory/writeback.py
# M8 · 写回编排：planner ↔ error_graph / error_ledger 桥接层
# 两段式（复用 engine/graph 的 pending/confirmed，不重造状态机）：
#   on_suspected  → 疑似：识别命中走 graph.ingest_error（uncertain→pending，复用）
#   on_confirmed  → 确认：记 ledger concept_confirmed（确认后入图谱由引擎各自负责）
#   on_repeated   → 惯犯：ledger.is_repeat_offender（阈值>=3 决策 B-乙）
# ============================================================

from typing import Any, Dict, List, Optional

from engine.memory.error_ledger import ErrorLedger, REPEAT_THRESHOLD


class Writeback:
    """把"判断偏误→写回图谱→惯犯信号"的编排，供 planner / 引擎调用。"""

    def __init__(self, graph=None, ledger: Optional[ErrorLedger] = None,
                 learner_id: str = "default", root: str = "data"):
        self.graph = graph
        self.ledger = ledger or ErrorLedger(learner_id=learner_id, root=root)

    # ---------------- 两段式：疑似 ----------------
    def on_suspected(self, bias: Dict[str, Any], event_key: str = "") -> Dict[str, Any]:
        """疑似偏误：识别引擎命中但未确认。走 graph.ingest_error（uncertain→pending，复用）。
        无 graph 时记 observation_error 到 ledger（不崩）。"""
        if self.graph is None:
            self.ledger.record("observation_error",
                               kp_id=bias.get("knowledge_point_id") or bias.get("fragment", ""),
                               signature=bias.get("fragment", ""),
                               evidence=bias.get("fragment", ""))
            return {"status": "ledger_only"}
        return self.graph.ingest_error(bias, event_key=event_key)

    # ---------------- 两段式：确认 ----------------
    def on_confirmed(self, kp_id: str, evidence: str = "") -> str:
        """确认：讲解/验证通过后调用，记 ledger concept_confirmed。
        （图谱侧 confirmed 状态由引擎各自写入，本层只负责事件账本。）"""
        return self.ledger.confirm(kp_id, evidence=evidence)

    # ---------------- 惯犯 ----------------
    def is_repeat_offender(self, kp_id: str, threshold: int = REPEAT_THRESHOLD) -> bool:
        return self.ledger.is_repeat_offender(kp_id, threshold=threshold)

    def repeated_count(self, kp_id: str) -> int:
        return self.ledger.repeated_count(kp_id)

    # ---------------- 复习联动 ----------------
    def review_queue(self) -> List[Dict[str, Any]]:
        """透传图谱复习队列（供 planner 主动提醒）。无图谱返回空。"""
        if self.graph is None:
            return []
        try:
            return self.graph.get_review_queue()
        except Exception:
            return []
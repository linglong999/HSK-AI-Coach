# ============================================================
# engine/memory/summarize.py
# M8 · 图谱+ledger → 画像常错点摘要（决策 C-乙：可按 focus_kp 按需注入）
# 借鉴 OpenMAIC extractLearnerMemory 规则桶 + 上限，state-context 按需取
# ============================================================

import time
from typing import Any, Dict, List, Optional

from engine.memory.error_ledger import ErrorLedger, MAX_EVENTS, REPEAT_THRESHOLD

MAX_LINES = 8   # 摘要最多行数（防上下文爆炸，控 token）


def _priority_of(item: Dict[str, Any]) -> float:
    return float(item.get("priority", 0.0))


def build_profile_summary(graph, ledger: Optional[ErrorLedger] = None,
                          focus_kp: Optional[str] = None,
                          repeat_threshold: int = REPEAT_THRESHOLD) -> str:
    """生成画像常错点摘要字符串。

    - 从 graph.get_review_queue() 取高优先级 KP（待复习）
    - 用 ledger.repeated_count 标记惯犯（>=阈值）
    - 决策 C-乙：focus_kp 给定 → 只保留该 KP 行（planner 按需注入）
    - 空图谱/空 ledger 安全返回空串
    """
    if graph is None:
        return ""
    try:
        queue = graph.get_review_queue()
    except Exception:
        queue = []
    if not queue:
        return ""
    ledger = ledger or ErrorLedger()

    lines_errors: List[str] = []
    lines_review: List[str] = []
    for item in queue:
        node = item.get("node", {})
        kp_id = node.get("id") or item.get("kp_id")
        if not kp_id:
            continue
        if focus_kp and kp_id != focus_kp:
            continue
        name = node.get("knowledge_point") or kp_id
        level = node.get("level", "")
        level_str = f"HSK{level}" if str(level).isdigit() else str(level)
        pr = _priority_of(item)
        repeated = ledger.repeated_count(kp_id)
        tag = ""
        if repeated >= repeat_threshold:
            tag = f" [重现×{repeated}]"
        lines_errors.append(f"- {name}（{level_str}）优先级{pr:.1f}{tag}")

    for line in lines_errors[:MAX_LINES]:
        lines_review.append(line)

    if not lines_review and focus_kp:
        # 该 KP 不在复习队列：尝试返回仅它的事件状态（供按需注入仍有点信息）
        repeated = ledger.repeated_count(focus_kp)
        if repeated:
            lines_review.append(f"- {focus_kp}：重复 {repeated} 次（尚未进入复习队列）")
    if not lines_review:
        return ""
    return "## 常错点 / 复习提醒\n" + "\n".join(lines_review)


def build_profile_facts(graph, ledger: Optional[ErrorLedger] = None,
                        limit: int = MAX_LINES,
                        repeat_threshold: int = REPEAT_THRESHOLD) -> List[Dict[str, Any]]:
    """画像常错点结构化事实（M8 汇合 M5：写入 LearnerMemory.profile.common_errors）。
    取图谱复习队列高优先级 KP + ledger 累计次数 + 最近一次证据；空图谱返回空列表。
    与 build_profile_summary 同源（queue + repeated_count），一个给人读、一个给落盘。"""
    if graph is None:
        return []
    try:
        queue = graph.get_review_queue()
    except Exception:
        return []
    ledger = ledger or ErrorLedger()
    facts: List[Dict[str, Any]] = []
    for item in queue[:limit]:
        node = item.get("node", {})
        kp_id = node.get("id") or item.get("kp_id")
        if not kp_id:
            continue
        repeated = ledger.repeated_count(kp_id)
        latest_evidence = ""
        for ev in reversed(ledger.recent(MAX_EVENTS)):
            if ev.get("kp_id") == kp_id and ev.get("kind") in ("observation_error",
                                                               "repeated_error"):
                latest_evidence = str(ev.get("evidence", ""))
                break
        facts.append({
            "kp_id": kp_id,
            "knowledge_point": node.get("knowledge_point") or kp_id,
            "level": node.get("level", ""),
            "repeated": repeated,
            "repeat_offender": repeated >= repeat_threshold,
            "priority": round(_priority_of(item), 4),
            "latest_evidence": latest_evidence,
            "updated_at": int(time.time()),
        })
    return facts
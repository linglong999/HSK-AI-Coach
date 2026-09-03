# ============================================================
# planner/fallback.py
# M4 Planner Loop · 确定性降级层
# 触发场景（不崩溃、给用户可理解回应）：
#   1) JSON 数组解析失败 / 空输出
#   2) 6 步循环达上限仍未收敛
#   3) 技能调用异常（已在 loop 内 catch，这里兜底消息）
# 原则：宁漏勿错 + 不静默透传坏结果（对齐项目铁律）
# ============================================================

from typing import Dict, Any

# 0.21 双语降级文案：native_lang 非 zh → 英文（面向英语母语学习者）
_MSG_MAP_ZH = {
    "parse": "我没能整理出一个可执行的回复，请把问题换种说法再问我一次。",
    "max_steps": "这个话题我继续展开意义不大，我们先在上面这里确认一下，你再告诉我下一步。",
    "skill_error": "我在处理时遇到了一个内部问题，请稍后再试。",
}
_MSG_MAP_EN = {
    "parse": "I couldn't put together a usable reply. Could you rephrase that and ask again?",
    "max_steps": "This topic isn't worth stretching further. Let's confirm what we have above, then you tell me the next step.",
    "skill_error": "I hit an internal problem while processing. Please try again in a moment.",
}


def fallback_reply(reason: str, partial_text: str = "",
                   native_lang: str = "") -> Dict[str, Any]:
    """构造降级回复。返回一个对用户安全、不含已失败技能结果的稳定对象。"""
    is_en = bool(native_lang and native_lang.lower() != "zh")
    msg_map = _MSG_MAP_EN if is_en else _MSG_MAP_ZH
    text = msg_map.get(reason, msg_map["parse"])
    return {
        "text": text,
        "fallback": True,
        "reason": reason,
        "partial": partial_text,
    }


def skill_error_reply(skill_name: str, err: str) -> Dict[str, Any]:
    """单个技能调用异常时喂给 LLM 的 tool_result（不崩，让模型重试或降级）。"""
    return {
        "type": "tool_result",
        "name": skill_name,
        "ok": False,
        "error": err[:200],
    }
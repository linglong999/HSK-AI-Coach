# ============================================================
# engine/generation/why.py
# 0.26 · 隐性"为什么"原理生成（折叠展示，默认不打扰场景节奏）
# authoring → LLM.chat_json_strict（供应商config覆盖）→ 宽容解析 → 降级兜底 []。
# 语义：对识别到的每一处偏误生成一句口语化的"为什么错、为什么这样改"；
# 可选 l1 母语归因（仅当与迁移假设相符且高置信，prompt 内约束，宁缺勿错）。
# 对齐铁律：不静默透传坏结果；任何异常/解析失败 → 返回空列表（前端无折叠块），不阻断对话。
# 纯标准库。
# ============================================================

import json
from typing import Any, Dict, List, Optional

from engine.generation.authoring import (
    WHY_FIELDS,
    build_why_prompt,
)
from engine.llm.client import LLMClient


_WHY_SYSTEM = (
    "你是一位 HSK 中文教学专家。你会拿到学习者写错的一小段中文及其修正，"
    "任务是用简短口语化的原理解释“为什么那样讲不对、为什么改成这样就对了”。"
    "只输出一个严格合法的 JSON object，结构与 user 提示词中的 schema 完全一致，"
    "不含任何多余文字、注释或代码块。"
)


def parse_why_items(parsed: Any) -> List[Dict[str, str]]:
    """宽容解析生成结果：{items:[{fragment,correction,reason,l1?}]} → 规范列表。
    非 list / 缺必需字段/字段类型错 → 跳过该条；结构完全不对返回空列表。"""
    if isinstance(parsed, dict):
        parsed = parsed.get("items")
    if not isinstance(parsed, list):
        return []
    out: List[Dict[str, str]] = []
    for it in parsed:
        if not isinstance(it, dict):
            continue
        row: Dict[str, str] = {}
        frag = it.get("fragment", "")
        corr = it.get("correction", "")
        reason = it.get("reason", "")
        row["fragment"] = str(frag or "")
        row["correction"] = str(corr or "")
        row["reason"] = str(reason or "")
        l1 = it.get("l1")
        if l1:
            row["l1"] = str(l1)
        # 宁缺勿错：没有 reason 的行没有教学意义，丢弃（LLM 缺 reason 不硬留）
        if row["reason"]:
            out.append({k: row[k] for k in row if k in WHY_FIELDS})
    return out


def generate_why(client: Optional[LLMClient] = None,
                 errors: Optional[List[Dict[str, Any]]] = None,
                 l1_hypotheses: Optional[List[Dict[str, Any]]] = None,
                 language_directive: str = "",
                 config: Optional[Dict[str, str]] = None,
                 max_repairs: int = 1) -> List[Dict[str, str]]:
    """为识别到的偏误生成"为什么"列表。errors 为空/LLM 失败/解析失败 → 返回 []（不阻断）。
    errors: [{fragment,correction,type,confidence}]；l1_hypotheses: [{l1_anchor}]。
    config: 请求级供应商覆盖（None → settings 全局配置）。"""
    if not errors:
        return []
    prompt = build_why_prompt(errors=errors, l1_hypotheses=l1_hypotheses,
                              language_directive=language_directive)
    if not prompt:
        return []
    client = client or LLMClient()
    for attempt in range(max(0, max_repairs) + 1):
        try:
            parsed = client.chat_json_strict(
                _WHY_SYSTEM, prompt, temperature=0.2, retries=1, config=config)
            items = parse_why_items(parsed)
            if items:
                return items
        except Exception as e:  # noqa: BLE001 网络/Key/解析失败：重试或兜底 []，不裸抛
            if attempt < max(0, max_repairs):
                continue
    return []


__all__ = ["WHY_FIELDS", "parse_why_items", "generate_why"]
# ============================================================
# planner/parser.py
# M4 Planner Loop · JSON 数组解析层
# 对齐 OpenMAIC stateless-generate：LLM 返回 JSON 数组文本
#   [ {"type":"action","name":...,"params":{...}}, {"type":"text","content":...} ]
# 自写 parser：剥代码围栏 → 自修复截断/残括号 → 逐项原生解析 → 增量数组
# 解析失败 → 抛 ParseError，由 fallback 降级（不崩溃）
# ============================================================

import json
import re
from typing import Any, Dict, List

from engine.llm.client import strip_code_fence


class ParseError(Exception):
    """JSON 数组解析失败。由 fallback 层捕获降级。"""


def _balance_braces(text: str) -> str:
    """暴力对齐左右中括号：统计差异，尝试尾部补全（OPENMAIC 式防截断）。"""
    if text.count("[") > text.count("]"):
        text = text.rstrip() + "]" * (text.count("[") - text.count("]"))
    if text.count("{") > text.count("}"):
        text = text + "}" * (text.count("{") - text.count("}"))
    return text


def _fix_trailing(text: str) -> str:
    """截断修复：去掉行尾孤立的键/值片段（无闭合引号或残缺），仅保结构完整前缀。"""
    # 逐字符找最后一个能闭合的 ']' 之前都保留；若缺尾巴括号由 _balance_braces 补
    last_close_bracket = text.rfind("]")
    if last_close_bracket != -1:
        text = text[: last_close_bracket + 1]
    return text


def _try_parse_json_array(text: str) -> List[Dict[str, Any]]:
    """尝试 stdlib 精确解析为数组。"""
    obj = json.loads(text)
    if isinstance(obj, list):
        return obj
    raise ValueError("非数组")


def _extract_first_array(text: str) -> str:
    """从头定位第一个 '[' 到最后一个 ']'，截取候选数组子串。"""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return ""
    return text[start : end + 1]


def parse_json_array(text: str) -> List[Dict[str, Any]]:
    """解析 LLM 输出的 JSON 数组文本，返回 item 列表（仅 action/text）。

    容错顺序：
      1) 剥代码围栏 + 原生 stdlib 解析
      2) 截取首个 '[...]' 子串再解析
      3) 括号/引号自修复后解析
    全部失败 → 抛 ParseError（含原始文本片段，供 fallback 提示）。
    """
    if not text or not text.strip():
        raise ParseError("空输出")

    cleaned = strip_code_fence(text)

    candidates = []
    # (a) 直接
    candidates.append(cleaned)
    # (b) 截取数组子串
    arr = _extract_first_array(cleaned)
    if arr:
        candidates.append(arr)
    # (c) 自修复：补括号/引号、截尾
    for c in list(candidates):
        fixed = _balance_braces(_fix_trailing(c))
        if fixed and fixed != c:
            candidates.append(fixed)

    last_err = None
    for cand in candidates:
        try:
            items = _try_parse_json_array(cand)
            return _normalize_items(items)
        except Exception as e:  # noqa: BLE001
            last_err = e

    # 兜底：裸对象形态（LLM 偶发省略数组包裹，实测 DeepSeek temperature>0 时
    # 约 1/6 概率）→ 归一为单元素数组
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, dict):
            item = _normalize_item(obj)
            if item:
                return [item]

    raise ParseError(f"无法解析 JSON 数组: {cleaned[:120]!r} ({last_err})")


def _normalize_item(it: Dict[str, Any]) -> Dict[str, Any]:
    """单 item 归一：接受标准形态 + LLM 常见变体，统一为 {type, ...} 标准形态。

    变体（实测 DeepSeek 无 few-shot 时的高频输出）：
      {"action": "identify_errors", "params": {...}}   → action 当了键名
      {"text": "对用户说的话"}                          → text 当了键名
    均转成 {"type": "action", "name": ..., "params": ...} / {"type": "text", "content": ...}
    """
    itype = it.get("type")
    if itype == "action" and it.get("name"):
        return it
    if itype == "text":
        return it
    if not itype:
        if "action" in it and isinstance(it["action"], str):
            return {"type": "action", "name": it["action"],
                    "params": it.get("params") or {}}
        if "text" in it and isinstance(it["text"], str):
            return {"type": "text", "content": it["text"]}
    return {}


def _normalize_items(raw_items: List[Any]) -> List[Dict[str, Any]]:
    """清洗：归一变体形态，过滤无法归一的项（不崩溃）。"""
    out: List[Dict[str, Any]] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        norm = _normalize_item(it)
        if norm:
            out.append(norm)
    return out
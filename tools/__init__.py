# ============================================================
# tools/__init__.py
# M9 · 薄统一门面 call_tool（供 planner 经 SkillRegistry 调用）
# - 白名单（per-learner/role，并入 call_tool，不设独立 access.py）
# - 分域分发：web_search / parse_document / asr / tts
# - 未配置 → {"ok":False,"status":"not_configured"}；绝不抛异常
# ============================================================

from typing import Any, Dict, Optional

from tools.registry import KNOWN_CALLABLE, ROLE_ALLOW

_HANDLERS = {}


def _get_handlers():
    global _HANDLERS
    if not _HANDLERS:
        from tools import web_search, parse_document, asr_providers, tts_providers
        _HANDLERS = {
            "web_search": web_search.search,
            "parse_document": parse_document.parse,
            "asr": asr_providers.transcribe,
            "tts": tts_providers.synthesize,
        }
    return _HANDLERS


def is_tool_allowed(name: str, role: str = "learner") -> bool:
    """白名单判定：per-role 默认放开 web_search + parse_document。"""
    allowed = ROLE_ALLOW.get(role, ROLE_ALLOW["learner"])
    return name in allowed


def call_tool(name: str, provider: Optional[str] = None, params: Optional[Dict[str, Any]] = None,
              learner_id: str = "default", role: str = "learner",
              transport: Optional[Any] = None) -> Dict[str, Any]:
    """统一门面（对齐 agent-design M9 / OpenMAIC unified facade）。

    1) 未知能力域 / 白名单外 → tool_not_allowed
    2) 分发到能力域路由；未配置 → not_configured
    3) 路由内已 try/except → 结构化 error，绝不抛异常
    """
    params = params or {}
    if name not in KNOWN_CALLABLE:
        return {"ok": False, "status": "tool_not_allowed",
                "message": f"未知工具: {name}"}
    if not is_tool_allowed(name, role):
        return {"ok": False, "status": "tool_not_allowed",
                "message": f"工具 '{name}' 不在角色 '{role}' 的白名单内"}

    handlers = _get_handlers()
    handler = handlers.get(name)
    if handler is None:
        return {"ok": False, "status": "not_configured",
                "message": f"工具 '{name}' 未实现"}

    kwargs = {"params": params}
    if name == "web_search":
        kwargs = {"provider_id": provider, "query": params.get("query", ""),
                  "max_results": params.get("max_results", 5)}
        if transport is not None:
            kwargs["transport"] = transport
    elif name in ("asr", "tts"):
        kwargs["provider_id"] = provider

    try:
        result = handler(**kwargs)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": "error", "message": f"工具 '{name}' 执行失败: {e}"}
    result = dict(result or {})
    result.setdefault("name", name)
    return result


__all__ = ["call_tool", "is_tool_allowed"]
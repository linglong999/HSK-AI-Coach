# ============================================================
# tools/web_search.py
# M9 · web 搜索路由（配置驱动，未配 key → not_configured）
# 对齐 OpenMAIC lib/web-search/RHEX：provider 路由函数 + 未配置 key 语义
# 落地：标准库 urllib；transport 可注入（对齐 planner llm_call 模式），测试零网络
# ============================================================

import json
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional

from tools.registry import check_ready, get_api_key

DEFAULT_MAX_RESULTS = 5


def _default_transport(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    """默认 transport：urllib POST JSON 请求（仅标准库）。
    抛异常由路由层 try/except 捕获 → 结构化 error（不崩）。"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _search_tavily(cfg: Dict[str, Any], query: str, max_results: int,
                   transport: Callable[..., Dict[str, Any]]) -> Dict[str, Any]:
    key = get_api_key(cfg)
    base = cfg.get("default_base_url", "https://api.tavily.com").rstrip("/")
    url = f"{base}/search"
    body = transport(url, {"Authorization": f"Bearer {key}",
                           "Content-Type": "application/json"},
                     {"query": query, "max_results": max_results})
    results = body.get("results", []) if isinstance(body, dict) else []
    return {
        "ok": True,
        "query": query,
        "results": [
            {"title": r.get("title", ""), "link": r.get("url", ""),
             "snippet": r.get("content", "")}
            for r in results
        ],
    }


def _search_brave(cfg: Dict[str, Any], query: str, max_results: int,
                  transport: Callable[..., Dict[str, Any]]) -> Dict[str, Any]:
    key = get_api_key(cfg)
    base = cfg.get("default_base_url", "https://api.search.brave.com").rstrip("/")
    qs = urllib.parse.urlencode({"q": query, "count": max_results})
    url = f"{base}/res/v1/web/search?{qs}"
    body = transport(url, {"X-Subscription-Token": key,
                           "Accept": "application/json"}, {})
    web = (body.get("web", {}) if isinstance(body, dict) else {}).get("results", [])
    return {
        "ok": True,
        "query": query,
        "results": [
            {"title": r.get("title", ""), "link": r.get("url", ""),
             "snippet": r.get("description", "")}
            for r in web
        ],
    }


_PROVIDER_HANDLERS = {
    "tavily": _search_tavily,
    "brave": _search_brave,
}


def search(provider_id: str, query: str, max_results: int = DEFAULT_MAX_RESULTS,
           transport: Optional[Callable[..., Dict[str, Any]]] = None) -> Dict[str, Any]:
    """web 搜索路由。未配置 → {"ok":False,"status":"not_configured"}（不崩）。"""
    transport = transport or _default_transport
    if not query or not query.strip():
        return {"ok": False, "status": "error", "message": "query 不能为空"}
    cfg, err = check_ready("web_search", provider_id)
    if err:
        return {"ok": False, "status": "not_configured", "message": err}
    handler = _PROVIDER_HANDLERS.get(cfg["id"])
    if handler is None:
        return {"ok": False, "status": "not_configured",
                "message": f"web_search provider '{cfg['id']}' 未实现"}
    try:
        return handler(cfg, query.strip(), max_results, transport)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": "error", "message": f"web 搜索失败: {e}"}
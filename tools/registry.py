# ============================================================
# tools/registry.py
# M9 工具扩展 · 分能力域 provider 注册表（配置驱动、默认关）
# 对齐 OpenMAIC：
#   - lib/web-search/constants.ts（provider 表独立于实现）
#   - lib/server/provider-config.ts（配置驱动 / disabled → not_configured）
# 落地：每个能力域一张 provider 表 + 配置驱动判定；零第三方依赖（仅标准库）
# ============================================================

import os
from typing import Any, Callable, Dict, Optional, Tuple

# 能力域（贴 agent-design M9：web_search / parse_document / asr / tts）
DOMAIN_NAMES = ("web_search", "parse_document", "asr", "tts")


def _env(*names: str, default: str = "") -> str:
    """按序取第一个非空环境变量（配置驱动：真实 env 优先，.env 由 settings 载入）。"""
    for n in names:
        v = os.getenv(n, "").strip()
        if v:
            return v
    return default


# ---------------- 能力域定义 ----------------
# provider 表字段（贴 OpenMAIC WebSearchProviderConfig）：
#   id / name / requires_key / api_key_env / default_base_url
#   call: (params, transport) -> {"ok":True,"data":...}，由各域路由函数填充
CAPABILITY_DOMAINS: Dict[str, Dict[str, Any]] = {
    "web_search": {
        "name": "网页搜索",
        # 配置驱动：WEB_SEARCH_PROVIDER 为空 → 默认关
        "default_provider": _env("WEB_SEARCH_PROVIDER", ""),
        "providers": {
            # 注册可选 provider 占位；未配 key → not_configured（宁漏勿错，不做脆弱 scrape）
            "tavily": {
                "id": "tavily",
                "name": "Tavily",
                "requires_key": True,
                "api_key_env": "TAVILY_API_KEY",
                "default_base_url": "https://api.tavily.com",
            },
            "brave": {
                "id": "brave",
                "name": "Brave Search",
                "requires_key": True,
                "api_key_env": "BRAVE_API_KEY",
                "default_base_url": "https://api.search.brave.com",
            },
        },
    },
    "parse_document": {
        "name": "文档解析",
        # 本地处理器：text 域（UTF-8/简单文档）无需外部 provider，默认可用
        "default_provider": "local",
        "providers": {
            "local": {
                "id": "local",
                "name": "本地文本解析",
                "requires_key": False,
                "api_key_env": "",
                "default_base_url": "",
            },
            # 复杂文档（PDF/Office）provider 位，默认关，未来可注册外部服务
        },
    },
    "asr": {
        "name": "语音识别",
        "default_provider": "",
        "providers": {
            # 仅 provider 位（默认关），供 future pronunciation_assessment 启用
        },
    },
    "tts": {
        "name": "语音合成",
        "default_provider": "",
        "providers": {
            # 仅 provider 位（默认关）
        },
    },
}

# ---------------- 白名单（贴 agent-design M9：并入 call_tool） ----------------
# per-learner/role 工具白名单，默认放开两个文字工具
DEFAULT_ALLOWED_TOOLS = ("web_search", "parse_document")
ROLE_ALLOW: Dict[str, tuple] = {
    "learner": DEFAULT_ALLOWED_TOOLS,
    "guest": (),          # 访客不放工具
    "admin": DEFAULT_ALLOWED_TOOLS + ("asr", "tts"),
}

# 未知能力域 → 视为未配置（宁漏勿错）
KNOWN_CALLABLE = {"web_search", "parse_document", "asr", "tts"}


# ---------------- 查询/判定 ----------------
def is_known(domain: str) -> bool:
    return domain in KNOWN_CALLABLE


def is_enabled(domain: str) -> bool:
    """该能力域是否启用。web_search 需配置 default_provider；parse_document 本地默认可用。"""
    if domain not in CAPABILITY_DOMAINS:
        return False
    dom = CAPABILITY_DOMAINS[domain]
    if not dom["providers"]:
        return False
    if domain == "parse_document":
        return True  # 本地文本解析免 key
    return bool(dom.get("default_provider"))


def resolve_provider(domain: str,
                     provider_id: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """返回 (provider_config, error)。
    - 未知域 / 能力域未注册 provider → (None, error)
    - provider_id 未指定 → 用能力域 default_provider
    - 指定 provider 但不在表内 → 明确 error，绝不 raise
    """
    dom = CAPABILITY_DOMAINS.get(domain)
    if dom is None:
        return None, f"未知能力域: {domain}"
    providers = dom["providers"]
    if not providers:
        return None, f"能力域 '{domain}' 未注册任何 provider（该域当前默认关）"

    pid = provider_id or dom.get("default_provider") or ""
    if pid not in providers:
        avail = ", ".join(sorted(providers.keys())) or "（无）"
        return None, f"能力域 '{domain}' 未配置/未启用（可用 provider: {avail}）"

    return providers[pid], ""


def get_api_key(cfg: Dict[str, Any]) -> str:
    """取 provider 的 api_key（配置驱动）。无 key 返回空串。"""
    env_name = cfg.get("api_key_env") or ""
    return os.getenv(env_name, "").strip()


def check_ready(domain: str, provider_id: Optional[str] = None) -> Tuple[Optional[Dict], str]:
    """整链就绪判定：返回 (cfg, error)。error 为 not_configured 语义（不 raise）。
    与 resolve_provider 区别：本函数额外校验 requires_key 是否满足。"""
    cfg, err = resolve_provider(domain, provider_id)
    if err:
        return None, err
    if cfg.get("requires_key") and not get_api_key(cfg):
        env = cfg.get("api_key_env") or "?"
        return None, f"{cfg.get('name', domain)} 未配置 API key（请在 .env 设置 {env}）"
    return cfg, ""
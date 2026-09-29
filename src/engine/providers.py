# ============================================================
# engine/providers.py
# 0.20 · 模型密钥管理（UI 内添加供应商，无需改 .env 重启）
# - 存储仅含 UI 添加的供应商（data/llm_providers.json，gitignore 挡住）
# - .env 密钥向后兼容：运行时虚拟为 id="env" 的默认供应商（不可删除）
# - 全部走 OpenAI 兼容协议：DeepSeek/Qwen/GLM/Kimi/Ollama 等同一格式
# ============================================================

import json
import ipaddress
import os
import secrets
import socket
import tempfile
import time
from urllib.parse import urlsplit
from typing import Any, Dict, List, Optional

from engine import provider_secret

ENV_PROVIDER_ID = "env"


def validate_provider_url(base_url: str, *, allow_local: bool = False,
                          resolve_dns: bool = True) -> str:
    """供应商网络出口预校验；公网发布仍需连接层 DNS 固定以消除重绑定窗口。"""
    value = str(base_url or "").strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("供应商 URL 格式无效") from exc
    if (not host or parsed.scheme not in {"https", "http"}
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or not parsed.netloc):
        raise ValueError("供应商 URL 仅允许无凭据、无查询参数的 HTTP(S) 地址")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("供应商 URL 端口无效")
    if any(ch.isspace() for ch in value) or "\\" in value:
        raise ValueError("供应商 URL 格式无效")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    is_local = host.lower() == "localhost" or (literal is not None and literal.is_loopback)
    if is_local:
        if not allow_local or parsed.scheme != "http":
            raise ValueError("本机 HTTP 供应商需显式开启 ALLOW_LOCAL_PROVIDER_URLS")
        return value
    if parsed.scheme != "https":
        raise ValueError("外部供应商必须使用 HTTPS")
    if literal is not None:
        if not literal.is_global:
            raise ValueError("供应商 URL 不允许私网、链路本地或保留地址")
        return value
    if host.lower().endswith((".localhost", ".local", ".internal")):
        raise ValueError("供应商 URL 不允许本地域名")
    if resolve_dns:
        try:
            addresses = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("供应商域名无法解析") from exc
        if not addresses or any(not ipaddress.ip_address(info[4][0]).is_global
                                for info in addresses):
            raise ValueError("供应商域名解析到非公网地址")
    return value


def _providers_path(root: str) -> str:
    return os.path.join(root, "llm_providers.json")


def load_store(root: str) -> Dict[str, Any]:
    """读取供应商存储；旧版 Windows 明文文件首次读取时原子迁移。"""
    path = _providers_path(root)
    if not os.path.exists(path):
        return {"providers": [], "default_id": None}
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("供应商配置文件无法读取；请先备份并修复，程序不会覆盖原文件") from exc
    if not isinstance(data, dict):
        raise RuntimeError("供应商配置文件格式无效；程序不会覆盖原文件")
    raw_providers = data.get("providers", [])
    if not isinstance(raw_providers, list):
        raise RuntimeError("供应商配置文件格式无效；程序不会覆盖原文件")
    providers = []
    migrate = False
    for item in raw_providers:
        if not isinstance(item, dict) or not item.get("id"):
            raise RuntimeError("供应商配置文件包含无效记录；程序不会覆盖原文件")
        record = dict(item)
        protected = record.pop("api_key_protected", None)
        if protected is not None:
            record["api_key"] = provider_secret.unprotect(str(protected))
        elif os.name == "nt" and record.get("api_key"):
            migrate = True
        providers.append(record)
    store = {"providers": providers, "default_id": data.get("default_id")}
    if migrate:
        save_store(root, store)
    return store


def save_store(root: str, store: Dict[str, Any]) -> None:
    """加密 Key 后原子落盘；失败时保留现有文件。"""
    os.makedirs(root, exist_ok=True)
    path = _providers_path(root)
    serializable = {"providers": [], "default_id": store.get("default_id")}
    for item in store.get("providers", []):
        record = dict(item)
        key = str(record.pop("api_key", ""))
        record.pop("api_key_protected", None)
        if os.name == "nt":
            record["api_key_protected"] = provider_secret.protect(key)
        else:
            record["api_key"] = key
        serializable["providers"].append(record)
    fd, tmp = tempfile.mkstemp(prefix=".llm_providers_", suffix=".tmp", dir=root)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(serializable, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


_PROVIDER_LABELS = {"deepseek": "DeepSeek", "qwen": "Qwen"}


def env_provider() -> Optional[Dict[str, Any]]:
    """`.env`/环境变量密钥 → 虚拟供应商（向后兼容；无 Key → None）。"""
    from config import settings
    base_url, api_key, model = settings.get_llm_config()
    if not api_key:
        return None
    label = _PROVIDER_LABELS.get(settings.LLM_PROVIDER, settings.LLM_PROVIDER)
    return {
        "id": ENV_PROVIDER_ID,
        "name": f"{label} · 默认（.env）",
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "source": "env",
    }


def effective_providers(root: str) -> List[Dict[str, Any]]:
    """env 虚拟供应商置顶 + UI 添加的供应商。"""
    out: List[Dict[str, Any]] = []
    ep = env_provider()
    if ep:
        out.append(ep)
    out.extend(load_store(root)["providers"])
    return out


def resolve_provider(root: str, provider_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """解析生效供应商：显式 id → 显式 default → env → 首个 UI 供应商。"""
    providers = effective_providers(root)
    by_id = {p["id"]: p for p in providers}
    if provider_id:
        return by_id.get(provider_id)
    default_id = load_store(root).get("default_id")
    if default_id and default_id in by_id:
        return by_id[default_id]
    return providers[0] if providers else None


def mask_key(key: str) -> str:
    """api_key 掩码：只露末 4 位（响应绝不含完整 Key）。"""
    if not key:
        return ""
    tail = key[-4:] if len(key) > 8 else "****"
    return key[:3] + "***" + tail if len(key) > 8 else "***" + tail


def masked(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": p["id"],
        "name": p.get("name", ""),
        "base_url": p.get("base_url", ""),
        "model": p.get("model", ""),
        "api_key_masked": mask_key(p.get("api_key", "")),
        "source": p.get("source", "ui"),
    }


def new_provider(name: str, base_url: str, api_key: str, model: str) -> Dict[str, Any]:
    return {
        "id": "p-" + secrets.token_hex(4),
        "name": name.strip(),
        "base_url": base_url.strip().rstrip("/"),
        "api_key": api_key.strip(),
        "model": model.strip(),
        "source": "ui",
    }


def test_provider(base_url: str, api_key: str, model: str,
                  timeout: int = 20) -> Dict[str, Any]:
    """连通性测试：最小 chat 请求（不落盘、不写图谱）。"""
    import httpx
    from config import settings
    from engine.llm.client import LLMClient
    t0 = time.time()
    try:
        safe_url = validate_provider_url(
            base_url, allow_local=settings.ALLOW_LOCAL_PROVIDER_URLS)
        cfg = {"base_url": safe_url, "api_key": api_key, "model": model}
        with httpx.Client(follow_redirects=False, trust_env=False,
                          timeout=timeout) as transport:
            out = LLMClient(http_client=transport, timeout=timeout).chat(
                [{"role": "user", "content": "回复：ok"}],
                max_tokens=8, temperature=0.0, config=cfg)
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000),
                "sample": str(out)[:40]}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)[:160]}
    except Exception:  # noqa: BLE001 不将 SDK 原始异常（可能含 URL/Key）透给浏览器
        return {"ok": False, "error": "供应商连接失败；请检查 URL、网络与 API Key"}

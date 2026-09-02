# ============================================================
# engine/providers.py
# 0.20 · BYOK 模型密钥管理（对标 Explore：UI 内添加供应商，无需改 .env 重启）
# - 存储仅含 UI 添加的供应商（data/llm_providers.json，gitignore 挡住）
# - .env 密钥向后兼容：运行时虚拟为 id="env" 的默认供应商（不可删除）
# - 全部走 OpenAI 兼容协议：DeepSeek/Qwen/GLM/Kimi/Ollama 等同一格式
# ============================================================

import json
import os
import secrets
import time
from typing import Any, Dict, List, Optional

ENV_PROVIDER_ID = "env"


def _providers_path(root: str) -> str:
    return os.path.join(root, "llm_providers.json")


def load_store(root: str) -> Dict[str, Any]:
    """读取供应商存储；文件缺失/损坏 → 空存储（fail-open，不阻断启动）。"""
    path = _providers_path(root)
    if not os.path.exists(path):
        return {"providers": [], "default_id": None}
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"providers": [], "default_id": None}
    if not isinstance(data, dict):
        return {"providers": [], "default_id": None}
    return {
        "providers": [p for p in data.get("providers", [])
                      if isinstance(p, dict) and p.get("id")],
        "default_id": data.get("default_id"),
    }


def save_store(root: str, store: Dict[str, Any]) -> None:
    """原子落盘（tmp + rename，与 LearnerMemory 同策略）。"""
    os.makedirs(root, exist_ok=True)
    path = _providers_path(root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


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
    from engine.llm.client import LLMClient
    cfg = {"base_url": base_url.rstrip("/"), "api_key": api_key, "model": model}
    t0 = time.time()
    try:
        out = LLMClient().chat(
            [{"role": "user", "content": "回复：ok"}],
            max_tokens=8, temperature=0.0, config=cfg)
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000),
                "sample": str(out)[:40]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}

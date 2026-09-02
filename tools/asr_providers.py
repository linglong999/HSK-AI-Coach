# ============================================================
# tools/asr_providers.py / tts_providers.py
# M9 · 语音 provider 位（仅表 + 接口，默认关）
# 供 future pronunciation_assessment 启用；当前 call_tool 一律 not_configured
# ============================================================

from typing import Any, Dict, Optional

# provider 位（默认关）。仅元信息 + 接口占位，不接真实后端。
ASR_PROVIDERS: Dict[str, Dict[str, Any]] = {
    # 例：passive_provider 由未来 ASR 引擎/后端注册
}

DEFAULT_PROVIDER = ""  # 空 → 默认关


def is_available() -> bool:
    return bool(DEFAULT_PROVIDER)


def transcribe(params: Optional[Dict[str, Any]] = None,
               provider_id: Optional[str] = None) -> Dict[str, Any]:
    """占位接口：未启用一律 not_configured（不崩）。"""
    return {"ok": False, "status": "not_configured",
            "message": "ASR 语音识别当前未配置/默认关，供未来 pronunciation_assessment 启用"}
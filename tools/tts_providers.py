# ============================================================
# tools/tts_providers.py
# M9 · TTS provider 位（仅表 + 接口，默认关）
# ============================================================

from typing import Any, Dict, Optional

TTS_PROVIDERS: Dict[str, Dict[str, Any]] = {
    # 例：provider 由未来 TTS 后端注册
}

DEFAULT_PROVIDER = ""  # 空 → 默认关


def is_available() -> bool:
    return bool(DEFAULT_PROVIDER)


def synthesize(params: Optional[Dict[str, Any]] = None,
               provider_id: Optional[str] = None) -> Dict[str, Any]:
    """占位接口：未启用一律 not_configured（不崩）。"""
    return {"ok": False, "status": "not_configured",
            "message": "TTS 语音合成当前未配置/默认关"}
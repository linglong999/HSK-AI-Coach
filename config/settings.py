# ============================================================
# HSK-AI-Coach 配置
# 所有可通过环境变量覆盖的配置集中在此，方便用户自填 API Key。
# 加载：python-dotenv（主流 .env 方案，支持插值/多行/类型收敛），
#       真实环境变量优先级高于 .env（override=False 不覆盖既有 env）。
# 接口约定：本模块保持**模块级常量**（settings.LLM_PROVIDER /
#   settings.DEEPSEEK_API_KEY / settings.get_llm_config()），
#   供调用点与测试按属性打桩 —— 勿重构成 dataclass 嵌套，否则破坏
#   mock.patch("config.settings.X") 的兼容。
# ============================================================

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根 = 本文件所在目录的上一级（.env 与 README 同级）
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH, override=False)


def _c(s: str) -> str:
    """收敛字符串配置：strip，空串回落默认。"""
    return s.strip() if isinstance(s, str) else (s or "")


def _i(s: str, default: int) -> int:
    """收敛为 int，非法/缺失回落默认。"""
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def _f(s: str, default: float) -> float:
    """收敛为 float，非法/缺失回落默认。"""
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _b(s: str, default: bool) -> bool:
    """收敛为 bool：1/true/yes/on → True，其余按 falsy。"""
    if isinstance(s, bool):
        return s
    v = str(s).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off", ""):
        return False
    return default


# ---------------- LLM 配置 ----------------
# 主模型：DeepSeek（默认引擎）
# 说明：开源后由用户自行填写 DEEPSEEK_API_KEY，本项目不承担任何 API 费用
LLM_PROVIDER = _c(os.getenv("LLM_PROVIDER", "deepseek"))  # deepseek | qwen

# DeepSeek 官方 API（OpenAI 兼容格式）
DEEPSEEK_API_KEY = _c(os.getenv("DEEPSEEK_API_KEY", ""))
DEEPSEEK_BASE_URL = _c(os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"))
DEEPSEEK_MODEL = _c(os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))

# 备选：千问 Qwen（DashScope 兼容格式）
QWEN_API_KEY = _c(os.getenv("QWEN_API_KEY", ""))
QWEN_BASE_URL = _c(os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
QWEN_MODEL = _c(os.getenv("QWEN_MODEL", "qwen-plus"))

# ---------------- 引擎参数 ----------------
# 偏误识别置信度阈值：低于该值不强制纠正，标记为"待确认"
CONFIDENCE_THRESHOLD = _f(os.getenv("CONFIDENCE_THRESHOLD", "0.85"), 0.85)

# 是否打印调试日志
DEBUG = _b(os.getenv("DEBUG", "1"), True)


def get_llm_config():
    """根据 provider 返回 (base_url, api_key, model) 三元组"""
    if LLM_PROVIDER == "qwen":
        return QWEN_BASE_URL, QWEN_API_KEY, QWEN_MODEL
    return DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL
# ============================================================
# HSK-AI-Coach 配置
# 所有可通过环境变量覆盖的配置集中在此，方便用户自填 API Key
# ============================================================

import os


def _load_dotenv(path: str) -> None:
    """零依赖 .env 加载（4.3 开源最短路径）：纯标准库解析 KEY=VALUE。
    真实环境变量优先于 .env；文件不存在/损坏时静默跳过（.env 永远是可选项）。"""
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


# 项目根 = config/ 的上一级（.env 与 README 同级）
_load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

# ---------------- LLM 配置 ----------------
# 主模型：DeepSeek（默认引擎）
# 说明：开源后由用户自行填写 DEEPSEEK_API_KEY，本项目不承担任何 API 费用
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")  # deepseek | qwen

# DeepSeek 官方 API（OpenAI 兼容格式）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# 备选：千问 Qwen（DashScope 兼容格式）
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus")

# ---------------- 引擎参数 ----------------
# 偏误识别置信度阈值：低于该值不强制纠正，标记为"待确认"
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.85"))

# 是否打印调试日志
DEBUG = os.getenv("DEBUG", "1") == "1"


def get_llm_config():
    """根据 provider 返回 (base_url, api_key, model) 三元组"""
    if LLM_PROVIDER == "qwen":
        return QWEN_BASE_URL, QWEN_API_KEY, QWEN_MODEL
    return DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL
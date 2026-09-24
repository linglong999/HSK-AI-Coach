# ============================================================
# HSK-AI-Coach 配置
# 所有可通过环境变量覆盖的配置集中在此，方便用户自填 API Key。
# 加载：pydantic-settings（当前策略同原 python-dotenv override=False：
#       真实环境变量优先级高于 .env，.env 高于字段默认值）。
# 接口约定：本模块保持**模块级常量**（settings.LLM_PROVIDER /
#   settings.DEEPSEEK_API_KEY / settings.get_llm_config()），
#   供调用点与测试按属性打桩 —— 勿重构成 dataclass 嵌套，否则破坏
#   mock.patch("config.settings.X") 的兼容。
# ============================================================

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.paths import PROJECT_ROOT

# 项目根 = 由 config/paths.PROJECT_ROOT 统一定位（.env 与 README 同级）
_ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE, extra="ignore", case_sensitive=False)

    # ---------------- LLM 配置 ----------------
    # 主模型：DeepSeek（默认引擎）
    # 说明：开源后由用户自行填写 DEEPSEEK_API_KEY，本项目不承担任何 API 费用
    LLM_PROVIDER: str = "deepseek"  # deepseek | qwen
    # DeepSeek 官方 API（OpenAI 兼容格式）
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    # 备选：千问 Qwen（DashScope 兼容格式）
    QWEN_API_KEY: str = ""
    QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_MODEL: str = "qwen-plus"

    # ---------------- 引擎参数 ----------------
    # 偏误识别置信度阈值：低于该值不强制纠正，标记为"待确认"
    CONFIDENCE_THRESHOLD: float = 0.85
    # 是否打印调试日志
    DEBUG: bool = True

    # ---------------- 邀请制内测（B7 S5） ----------------
    # 逗号分隔的 open_id 白名单；空 = 邀请闸门关闭（默认，不拦截任何请求）。
    # 非空 = 0→1 邀请制开启：未带有效 open_id 的请求只出邀请页、不公开页面/数据。
    INVITE_ALLOWLIST: str = ""

    @field_validator("CONFIDENCE_THRESHOLD", mode="before")
    @classmethod
    def _fallback_conf(cls, v):
        """保旧行为：非法值静默回落默认，不炸 ValidationError。"""
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.85


settings = Settings()

# 模块级常量 = settings 快照导出（名字/语义与现状逐一同名）
LLM_PROVIDER = settings.LLM_PROVIDER
DEEPSEEK_API_KEY = settings.DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL = settings.DEEPSEEK_BASE_URL
DEEPSEEK_MODEL = settings.DEEPSEEK_MODEL
QWEN_API_KEY = settings.QWEN_API_KEY
QWEN_BASE_URL = settings.QWEN_BASE_URL
QWEN_MODEL = settings.QWEN_MODEL
CONFIDENCE_THRESHOLD = settings.CONFIDENCE_THRESHOLD
DEBUG = settings.DEBUG
INVITE_ALLOWLIST = settings.INVITE_ALLOWLIST


def get_llm_config():
    """根据 provider 返回 (base_url, api_key, model) 三元组。

    仍读模块级常量（非 settings.xxx）——保 mock.patch 后本函数跟随变化。
    """
    if LLM_PROVIDER == "qwen":
        return QWEN_BASE_URL, QWEN_API_KEY, QWEN_MODEL
    return DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL
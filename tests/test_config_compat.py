# ============================================================
# C3 config pydantic v2 兼容层 · 四锚点专项
# 覆盖：
#   锚点① 模块路径/属性名/函数签名零变化（config.settings 全属性 + get_llm_config 无参）
#   锚点② get_llm_config() 引用模块级常量 → mock.patch("config.settings.X") 跟随
#   锚点③ env 优先级等价：真实 env > .env > 默认值（pydantic-settings 原生）
#   锚点④ 非法值回落默认由 field_validator 保旧语义；bool 原生解析 1/on/false
# 运行: python -m unittest tests.test_config_compat -v
# ============================================================

import inspect
import os
import tempfile
import unittest
from unittest import mock

import config.settings as cs
from config.settings import Settings

_CONSTANTS = (
    "LLM_PROVIDER", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
    "QWEN_API_KEY", "QWEN_BASE_URL", "QWEN_MODEL", "CONFIDENCE_THRESHOLD", "DEBUG",
)


class ApiCompatTest(unittest.TestCase):
    """锚点①：路径/属性/签名零变化 —— 调用点与测试按 config.settings.X 打桩不破。"""

    def test_module_level_constants_exist(self):
        for name in _CONSTANTS:
            self.assertTrue(hasattr(cs, name), f"config.settings 缺 {name}")

    def test_singleton_instance_attrs_exist(self):
        for name in _CONSTANTS:
            self.assertTrue(hasattr(cs.settings, name),
                            f"settings 实例缺 {name}")

    def test_get_llm_config_signature(self):
        self.assertTrue(callable(cs.get_llm_config))
        sig = inspect.signature(cs.get_llm_config)
        self.assertEqual(list(sig.parameters), [])


class PatchChainTest(unittest.TestCase):
    """锚点②：patch 模块级常量 → get_llm_config() 跟随。"""

    def test_patch_qwen_follows(self):
        with mock.patch("config.settings.LLM_PROVIDER", "qwen"), \
             mock.patch("config.settings.QWEN_BASE_URL", "qb"), \
             mock.patch("config.settings.QWEN_API_KEY", "qk"), \
             mock.patch("config.settings.QWEN_MODEL", "qm"):
            self.assertEqual(cs.get_llm_config(), ("qb", "qk", "qm"))

    def test_patch_deepseek_follows(self):
        with mock.patch("config.settings.LLM_PROVIDER", "deepseek"), \
             mock.patch("config.settings.DEEPSEEK_BASE_URL", "db"), \
             mock.patch("config.settings.DEEPSEEK_API_KEY", "dk"), \
             mock.patch("config.settings.DEEPSEEK_MODEL", "dm"):
            self.assertEqual(cs.get_llm_config(), ("db", "dk", "dm"))

    def test_unknown_provider_defaults_to_deepseek(self):
        with mock.patch("config.settings.LLM_PROVIDER", "other"), \
             mock.patch("config.settings.DEEPSEEK_BASE_URL", "db"), \
             mock.patch("config.settings.DEEPSEEK_API_KEY", "dk"), \
             mock.patch("config.settings.DEEPSEEK_MODEL", "dm"):
            self.assertEqual(cs.get_llm_config(), ("db", "dk", "dm"))


class EnvPriorityTest(unittest.TestCase):
    """锚点③：真实 env > .env > 默认值。用 _env_file 构造参数隔离实例，不污染单例。"""

    @staticmethod
    def _tmp_env(content=""):
        d = tempfile.mkdtemp()
        p = os.path.join(d, ".env")
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return p

    def test_default_when_nothing_set(self):
        s = Settings(_env_file=self._tmp_env())
        self.assertEqual(s.DEEPSEEK_API_KEY, "")

    def test_dotenv_overrides_default(self):
        s = Settings(_env_file=self._tmp_env("DEEPSEEK_API_KEY=dotenv-key\n"))
        self.assertEqual(s.DEEPSEEK_API_KEY, "dotenv-key")

    def test_real_env_overrides_dotenv(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "real-env-key"}):
            s = Settings(_env_file=self._tmp_env("DEEPSEEK_API_KEY=dotenv-key\n"))
        self.assertEqual(s.DEEPSEEK_API_KEY, "real-env-key")


class ValidationCompatTest(unittest.TestCase):
    """锚点④：非法值回归默认；bool 原生解析。"""

    def test_invalid_confidence_falls_back_default(self):
        self.assertEqual(Settings(CONFIDENCE_THRESHOLD="abc").CONFIDENCE_THRESHOLD,
                         0.85)

    def test_numeric_string_confidence_parses(self):
        self.assertEqual(Settings(CONFIDENCE_THRESHOLD="0.7").CONFIDENCE_THRESHOLD,
                         0.7)

    def test_float_confidence_passthrough(self):
        self.assertEqual(Settings(CONFIDENCE_THRESHOLD=0.95).CONFIDENCE_THRESHOLD,
                         0.95)

    def test_bool_variants_parse(self):
        self.assertIs(Settings(DEBUG="on").DEBUG, True)
        self.assertIs(Settings(DEBUG="1").DEBUG, True)
        self.assertIs(Settings(DEBUG="false").DEBUG, False)


if __name__ == "__main__":
    unittest.main()
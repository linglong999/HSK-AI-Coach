# ============================================================
# M9 工具扩展 · 回归测试（tests/test_m9.py）
# 覆盖：tools/registry、call_tool 门面/白名单、web_search（mockable transport）、
#      parse_document、asr/tts 留位、skill wrapper、build_registry 可见性
# ============================================================

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills import build_registry
from tools import call_tool, is_tool_allowed
import tools.registry as registry
from tools.web_search import search as web_search_search
from tools.parse_document import parse as doc_parse


class TestRegistryConfig(unittest.TestCase):
    """能力域注册表：配置驱动、默认关。"""

    def test_web_search_disabled_by_default(self):
        # 未设 WEB_SEARCH_PROVIDER → web_search 默认关（is_enabled False）
        with mock.patch.dict(os.environ, {}, clear=False):
            if os.getenv("WEB_SEARCH_PROVIDER"):
                self.skipTest("环境已配置 WEB_SEARCH_PROVIDER")
            self.assertFalse(registry.is_enabled("web_search"))
            cfg, err = registry.check_ready("web_search", "tavily")
            # 即便指定 provider，因无 key 也是 not_configured 语义
            if cfg:
                self.assertEqual(err, "")
            else:
                self.assertTrue("未配置" in err or "API key" in err)

    def test_unknown_domain_error(self):
        cfg, err = registry.resolve_provider("no_such_domain")
        self.assertIsNone(cfg)
        self.assertIn("未知能力域", err)

    def test_parse_document_local_enabled(self):
        # parse_document 本地文本解析默认可用（免 key）
        self.assertTrue(registry.is_enabled("parse_document"))

    def test_asr_tts_not_enabled(self):
        self.assertFalse(registry.is_enabled("asr"))
        self.assertFalse(registry.is_enabled("tts"))


class TestCallToolFacade(unittest.TestCase):
    """统一门面：白名单 + 分域分发 + 不崩。"""

    def test_unknown_tool_not_allowed(self):
        res = call_tool("no_such_tool")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "tool_not_allowed")

    def test_asr_default_not_configured(self):
        # admin 白名单授权 asr/tts，但 provider 位未配置 → not_configured（留位默认关）
        res = call_tool("asr", role="admin")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "not_configured")
        # 默认关不影响零依赖，不抛异常
        self.assertIn("message", res)

    def test_tts_default_not_configured(self):
        res = call_tool("tts", role="admin")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "not_configured")

    def test_whitelist_role(self):
        # 默认 learner 放开 web_search/parse_document
        self.assertTrue(is_tool_allowed("web_search", "learner"))
        self.assertTrue(is_tool_allowed("parse_document", "learner"))
        # guest 不放工具
        self.assertFalse(is_tool_allowed("web_search", "guest"))

    def test_guest_web_search_not_allowed(self):
        res = call_tool("web_search", params={"query": "量词"},
                        role="guest")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "tool_not_allowed")


class TestWebSearch(unittest.TestCase):
    """web_search：未配 key → not_configured；配了（mockable transport）→ 结果。"""

    def test_no_key_not_configured(self):
        # 指定 provider 但有 provider 无 key → 仍 not_configured（配置驱动默认关）
        with mock.patch.dict(os.environ, {"WEB_SEARCH_PROVIDER": "tavily"},
                             clear=False):
            if os.getenv("TAVILY_API_KEY"):
                self.skipTest("环境已配置 TAVILY_API_KEY")
            res = call_tool("web_search", provider="tavily",
                            params={"query": "量词"}, role="learner")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "not_configured")

    def test_with_transport_returns_results(self):
        # 注入 mockable transport + patch env（模拟已配置 provider+key）→ 验证"配了可用"
        def fake_transport(url, headers, payload):
            return {"results": [{"title": "T", "url": "http://x", "content": "snippet"}]}
        with mock.patch.dict(os.environ,
                             {"WEB_SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "fake"},
                             clear=False):
            res = call_tool("web_search", provider="tavily",
                            params={"query": "把字句", "max_results": 5},
                            role="learner", transport=fake_transport)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["results"][0]["link"], "http://x")

    def test_empty_query_error(self):
        res = web_search_search("tavily", "  ")
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "error")

    def test_http_error_not_crash(self):
        def boom(url, headers, payload):
            raise RuntimeError("网络失败")
        # 配了 key + transport 抛异常 → 路由捕获 → 结构化 error，不崩
        with mock.patch.dict(os.environ,
                             {"WEB_SEARCH_PROVIDER": "tavily", "TAVILY_API_KEY": "fake"},
                             clear=False):
            res = call_tool("web_search", provider="tavily",
                            params={"query": "苹果"}, role="learner",
                            transport=boom)
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "error")


class TestParseDocument(unittest.TestCase):
    """parse_document：本地 text/markdown/html；复杂格式 not_configured。"""

    def test_text(self):
        res = doc_parse({"text": "你好 世界", "format": "text"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["blocks"][0]["text"], "你好 世界")

    def test_markdown(self):
        res = doc_parse({"text": "# 标题\n正文", "format": "markdown"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["blocks"][0]["type"], "markdown")

    def test_simple_html_strips_tags(self):
        res = doc_parse({"text": "<p>第一段</p><script>var x=1</script><p>第二段</p>",
                         "format": "html"})
        self.assertTrue(res["ok"])
        self.assertIn("第一段", res["blocks"][0]["text"])
        self.assertNotIn("var x", res["blocks"][0]["text"])

    def test_complex_format_not_configured(self):
        res = doc_parse({"text": "x", "format": "pdf"})
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "not_configured")

    def test_missing_text_error(self):
        res = doc_parse({})
        self.assertFalse(res["ok"])
        self.assertEqual(res["status"], "error")


class TestSkillWrapper(unittest.TestCase):
    """两个薄 skill wrapper + build_registry 可见性。"""

    def test_build_registry_has_tools(self):
        reg = build_registry()
        names = reg.all_names()
        self.assertIn("web_search", names)
        self.assertIn("parse_document", names)

    def test_manifest(self):
        reg = build_registry()
        m = reg.get("web_search").to_manifest()
        self.assertEqual(m["name"], "web_search")
        self.assertIn("query", m["input_schema"]["properties"])

    def test_run_parse_document(self):
        reg = build_registry()
        res = reg.get("parse_document").run({"text": "这是我的作业。", "format": "text"})
        self.assertTrue(res["ok"])
        self.assertIn("这是我的作业", res["blocks"][0]["text"])

    def test_run_web_search_wrapper_no_key(self):
        reg = build_registry()
        res = reg.get("web_search").run({"query": "量词",
                                         "role": "learner"})
        self.assertFalse(res["ok"])
        self.assertIn(res.get("status"), ("not_configured", "tool_not_allowed", "error"))


if __name__ == "__main__":
    unittest.main()
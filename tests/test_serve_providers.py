# ============================================================
# 0.20 BYOK 模型密钥 · 端到端 + 单元测试
# 覆盖：
#   - engine/providers.py：mask_key 掩码 / 存取原子落盘 / resolve 默认链 / env 虚拟供应商
#   - HTTP 端点：GET 列表（掩码，不泄漏完整 Key）/ POST 添加 / 设默认 / DELETE 删除
#   - 连通测试：按 id 与按字段（patch LLMClient.chat，零网络）
#   - dialog 供应商路由：provider_id → planner 绑定对应 config（model 断言）；
#     未知 provider_id → 400 unknown_provider；无任何供应商 → 400 llm_not_configured
#   - LLMClient json_mode 400 兜底：拒绝 response_format 的供应商自动降级重试
# 运行: python -m unittest tests.test_serve_providers -v
# ============================================================

import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import settings
from engine.graph.error_graph import ErrorGraph
from engine.llm.client import LLMClient
from engine.serve import make_handler


# ---------------- 测试替身 ----------------

class FakeRouter:
    learner_id = "prov_user"

    def __init__(self):
        self.graph = ErrorGraph("prov_test")
        self.recognizer = None
        self.verifier = None
        self.explainer = None


def _start_server(router, dialog_llm=None):
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html>providers</html>")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", port),
        make_handler(router, tmp, dialog_llm=dialog_llm, memory_root=tmp))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port, tmp


def _req(port, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    c.request(method, path,
              body.encode() if isinstance(body, str) else
              (json.dumps(body).encode() if body else None), headers)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw.decode())
    except Exception:  # noqa: BLE001
        return r.status, None


def _fake_chat_factory(captured):
    def fake_chat(self, messages, temperature=0.3, max_tokens=2000,
                  json_mode=False, config=None):
        captured.append({"config": config, "json_mode": json_mode})
        return '[{"type":"text","content":"模型回复。"}]'
    return fake_chat


# ---------------- 单元：providers 存储层 ----------------

class ProviderStoreTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_mask_key(self):
        from engine import providers as prov
        self.assertEqual(prov.mask_key("sk-1234567890abcdef"), "sk-***cdef")
        self.assertTrue(prov.mask_key("short").startswith("***"))
        self.assertEqual(prov.mask_key(""), "")

    def test_save_load_roundtrip(self):
        from engine import providers as prov
        store = {"providers": [prov.new_provider("T", "http://x/v1", "sk-k", "m")],
                 "default_id": None}
        prov.save_store(self.root, store)
        loaded = prov.load_store(self.root)
        self.assertEqual(len(loaded["providers"]), 1)
        self.assertEqual(loaded["providers"][0]["model"], "m")
        self.assertTrue(os.path.exists(os.path.join(self.root, "llm_providers.json")))

    def test_corrupt_file_fail_open(self):
        from engine import providers as prov
        with open(os.path.join(self.root, "llm_providers.json"), "w") as f:
            f.write("not json{{{")
        self.assertEqual(prov.load_store(self.root)["providers"], [])

    def test_resolve_chain_explicit_default_first(self):
        from engine import providers as prov
        a = prov.new_provider("A", "http://a/v1", "k", "model-a")
        b = prov.new_provider("B", "http://b/v1", "k", "model-b")
        prov.save_store(self.root, {"providers": [a, b], "default_id": b["id"]})
        with mock.patch.object(settings, "DEEPSEEK_API_KEY", ""), \
             mock.patch.object(settings, "QWEN_API_KEY", ""):
            self.assertEqual(prov.resolve_provider(self.root, None)["model"],
                             "model-b")
            self.assertEqual(prov.resolve_provider(self.root, a["id"])["model"],
                             "model-a")

    def test_resolve_chain_no_default_falls_to_first(self):
        from engine import providers as prov
        a = prov.new_provider("A", "http://a/v1", "k", "model-a")
        prov.save_store(self.root, {"providers": [a], "default_id": None})
        with mock.patch.object(settings, "DEEPSEEK_API_KEY", ""), \
             mock.patch.object(settings, "QWEN_API_KEY", ""):
            self.assertEqual(prov.resolve_provider(self.root, None)["model"],
                             "model-a")

    def test_env_provider_virtual(self):
        from engine import providers as prov
        with mock.patch.object(settings, "DEEPSEEK_API_KEY", "sk-envtest-1234567890"), \
             mock.patch.object(settings, "LLM_PROVIDER", "deepseek"):
            p = prov.env_provider()
            self.assertIsNotNone(p)
            self.assertEqual(p["id"], "env")
            self.assertIn(".env", p["name"])
            # 无存储时 env 即默认
            self.assertEqual(prov.resolve_provider(self.root, None)["id"], "env")
        with mock.patch.object(settings, "DEEPSEEK_API_KEY", ""), \
             mock.patch.object(settings, "QWEN_API_KEY", ""):
            self.assertIsNone(prov.env_provider())


# ---------------- 端到端：供应商 CRUD + dialog 路由 ----------------

class ProvidersEndpointTest(unittest.TestCase):
    """真实 planner 路径（无 dialog_llm 注入），LLMClient.chat 全局 mock（零网络）。"""

    @classmethod
    def setUpClass(cls):
        cls.router = FakeRouter()
        cls.httpd, cls.port, cls.tmp = _start_server(cls.router)
        cls.captured = []
        cls.p_env = mock.patch.object(
            settings, "DEEPSEEK_API_KEY", ""), mock.patch.object(
            settings, "QWEN_API_KEY", "")
        for p in cls.p_env:
            p.start()
        cls.p_chat = mock.patch.object(
            LLMClient, "chat", _fake_chat_factory(cls.captured))
        cls.p_chat.start()

    @classmethod
    def tearDownClass(cls):
        cls.p_chat.stop()
        for p in cls.p_env:
            p.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        g = os.path.join(_PROJECT_ROOT, "data", "graph_prov_test.json")
        if os.path.exists(g):
            os.remove(g)

    def test_01_empty_list_no_env(self):
        st, d = _req(self.port, "GET", "/api/providers")
        self.assertEqual(st, 200)
        self.assertEqual(d["providers"], [])
        self.assertIsNone(d["default_id"])

    def test_02_add_first_becomes_default(self):
        st, d = _req(self.port, "POST", "/api/providers",
                     {"name": "A", "base_url": "http://a/v1",
                      "api_key": "sk-aaaa11112222", "model": "model-a"})
        self.assertEqual(st, 200)
        self.assertTrue(d["ok"])
        pid = d["provider"]["id"]
        st, d = _req(self.port, "GET", "/api/providers")
        self.assertEqual(d["default_id"], pid)   # 无 env → 首个自动默认
        self.assertIn("***", d["providers"][0]["api_key_masked"])
        self.assertNotIn("sk-aaaa11112222", json.dumps(d))   # 完整 Key 绝不出现在响应

    def test_03_dialog_default_provider_bound(self):
        self.captured.clear()
        st, d = _req(self.port, "POST", "/api/dialog",
                     {"text": "你好", "conversation_id": "prov-t3"})
        self.assertEqual(st, 200)
        self.assertEqual(d["provider"]["model"], "model-a")
        self.assertEqual(self.captured[-1]["config"]["model"], "model-a")
        self.assertEqual(self.captured[-1]["config"]["base_url"], "http://a/v1")

    def test_04_dialog_explicit_provider_override(self):
        st, d = _req(self.port, "POST", "/api/providers",
                     {"name": "B", "base_url": "http://b/v1",
                      "api_key": "sk-bbbb33334444", "model": "model-b"})
        pid_b = d["provider"]["id"]
        self.captured.clear()
        st, d = _req(self.port, "POST", "/api/dialog",
                     {"text": "你好", "conversation_id": "prov-t4",
                      "provider_id": pid_b})
        self.assertEqual(st, 200)
        self.assertEqual(d["provider"]["model"], "model-b")
        self.assertEqual(self.captured[-1]["config"]["model"], "model-b")

    def test_05_dialog_unknown_provider_400(self):
        st, d = _req(self.port, "POST", "/api/dialog",
                     {"text": "你好", "provider_id": "p-nonexist"})
        self.assertEqual(st, 400)
        self.assertEqual(d["code"], "unknown_provider")

    def test_06_add_missing_field_400(self):
        st, d = _req(self.port, "POST", "/api/providers",
                     {"name": "X", "base_url": "http://x/v1"})
        self.assertEqual(st, 400)

    def test_07_test_endpoint_by_fields_and_id(self):
        st, d = _req(self.port, "POST", "/api/providers/test",
                     {"base_url": "http://a/v1", "api_key": "k",
                      "model": "model-a"})
        self.assertEqual(st, 200)
        self.assertTrue(d["ok"])
        self.assertIn("latency_ms", d)
        st, d = _req(self.port, "GET", "/api/providers")
        pid = d["default_id"]
        st, d = _req(self.port, "POST", "/api/providers/test", {"id": pid})
        self.assertEqual(st, 200)
        self.assertTrue(d["ok"])
        st, d = _req(self.port, "POST", "/api/providers/test", {"id": "p-nope"})
        self.assertEqual(st, 404)

    def test_08_default_switch_and_fallback(self):
        st, d = _req(self.port, "GET", "/api/providers")
        providers = d["providers"]
        pid_b = next(p["id"] for p in providers if p["model"] == "model-b")
        st, d = _req(self.port, "POST", "/api/providers/default", {"id": pid_b})
        self.assertEqual(st, 200)
        self.captured.clear()
        st, d = _req(self.port, "POST", "/api/dialog",
                     {"text": "你好", "conversation_id": "prov-t8"})
        self.assertEqual(d["provider"]["model"], "model-b")
        st, d = _req(self.port, "POST", "/api/providers/default",
                     {"id": "p-nope"})
        self.assertEqual(st, 404)

    def test_09_delete_and_default_cleared(self):
        st, d = _req(self.port, "GET", "/api/providers")
        providers = d["providers"]
        pid_b = next(p["id"] for p in providers if p["model"] == "model-b")
        st, d = _req(self.port, "DELETE",
                     "/api/providers?id=" + pid_b)
        self.assertEqual(st, 200)
        # 默认被删 → 回落首个（model-a）
        st, d = _req(self.port, "GET", "/api/providers")
        self.assertEqual(d["default_id"],
                         next(p["id"] for p in d["providers"]
                              if p["model"] == "model-a"))
        # 删除不存在的 → 404
        st, d = _req(self.port, "DELETE", "/api/providers?id=" + pid_b)
        self.assertEqual(st, 404)

    def test_10_persistence_across_restart(self):
        st, d = _req(self.port, "GET", "/api/providers")
        before = len(d["providers"])
        self.assertGreaterEqual(before, 1)
        # 同 memory_root 新 handler 模拟重启 → 存储仍在
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port2 = s.getsockname()[1]
        s.close()
        httpd2 = ThreadingHTTPServer(
            ("127.0.0.1", port2),
            make_handler(FakeRouter(), self.tmp, memory_root=self.tmp))
        threading.Thread(target=httpd2.serve_forever, daemon=True).start()
        try:
            st, d = _req(port2, "GET", "/api/providers")
            self.assertEqual(len(d["providers"]), before)
        finally:
            httpd2.shutdown()
            httpd2.server_close()


class EnvCompatTest(unittest.TestCase):
    """.env 有 Key → 虚拟供应商置顶为默认，不可删除。"""

    @classmethod
    def setUpClass(cls):
        cls.router = FakeRouter()
        cls.httpd, cls.port, cls.tmp = _start_server(cls.router)
        cls.patches = [
            mock.patch.object(settings, "DEEPSEEK_API_KEY", "sk-envsecret9999"),
            mock.patch.object(settings, "QWEN_API_KEY", ""),
            mock.patch.object(settings, "LLM_PROVIDER", "deepseek"),
        ]
        for p in cls.patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_env_provider_top_default_undeletable(self):
        st, d = _req(self.port, "GET", "/api/providers")
        self.assertEqual(st, 200)
        envs = [p for p in d["providers"] if p["source"] == "env"]
        self.assertEqual(len(envs), 1)
        self.assertEqual(d["default_id"], "env")
        self.assertNotIn("sk-envsecret9999", json.dumps(d))
        st, d = _req(self.port, "DELETE", "/api/providers?id=env")
        self.assertEqual(st, 404)
        # env 在场时添加供应商不抢占默认
        _req(self.port, "POST", "/api/providers",
             {"name": "C", "base_url": "http://c/v1", "api_key": "k",
              "model": "model-c"})
        st, d = _req(self.port, "GET", "/api/providers")
        self.assertEqual(d["default_id"], "env")


# ---------------- 单元：LLMClient json_mode 400 兜底 ----------------

class _FakeResponse:
    def __init__(self, body):
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeHttp:
    """urllib.request 替身：捕获 payload；脚本化返回/抛错。"""

    def __init__(self):
        self.payloads = []
        self.script = []   # 每项：{"raise": HTTPError} 或 {"body": str}

    def Request(self, url, data=None, headers=None, method=None):
        return {"url": url, "data": data, "headers": headers}

    def urlopen(self, req, timeout=60):
        payload = json.loads(req["data"].decode())
        self.payloads.append(payload)
        step = self.script.pop(0) if self.script else {"body": "{}"}
        if "raise" in step:
            raise step["raise"]
        content = step.get("content", "ok")
        return _FakeResponse(json.dumps(
            {"choices": [{"message": {"content": content}}]}))


class JsonModeFallbackTest(unittest.TestCase):

    def _client(self, fake):
        c = LLMClient()
        c._request = fake
        return c

    def test_400_falls_back_without_response_format(self):
        import urllib.error
        fake = _FakeHttp()
        fake.script = [
            {"raise": urllib.error.HTTPError(
                "u", 400, "response_format not supported", None, None)},
            {"content": '{"a":1}'},
        ]
        c = self._client(fake)
        out = c.chat([{"role": "user", "content": "hi"}],
                     json_mode=True, config={"base_url": "http://x/v1",
                                             "api_key": "k", "model": "m"})
        self.assertEqual(out, '{"a":1}')
        self.assertIn("response_format", fake.payloads[0])
        self.assertNotIn("response_format", fake.payloads[1])   # 降级重试去掉参数

    def test_400_without_json_mode_no_retry(self):
        import urllib.error
        fake = _FakeHttp()
        fake.script = [
            {"raise": urllib.error.HTTPError("u", 400, "bad", None, None)},
        ]
        c = self._client(fake)
        with self.assertRaises(RuntimeError) as ctx:
            c.chat([{"role": "user", "content": "hi"}],
                   config={"base_url": "http://x/v1", "api_key": "k",
                           "model": "m"})
        self.assertIn("HTTP 400", str(ctx.exception))
        self.assertEqual(len(fake.payloads), 1)

    def test_fallback_also_fails_raises_descriptive(self):
        import urllib.error
        fake = _FakeHttp()
        fake.script = [
            {"raise": urllib.error.HTTPError("u", 400, "bad", None, None)},
            {"raise": urllib.error.HTTPError("u", 401, "unauthorized", None, None)},
        ]
        c = self._client(fake)
        with self.assertRaises(RuntimeError) as ctx:
            c.chat([{"role": "user", "content": "hi"}],
                   json_mode=True,
                   config={"base_url": "http://x/v1", "api_key": "k",
                           "model": "m"})
        self.assertIn("json_mode 降级重试仍失败", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

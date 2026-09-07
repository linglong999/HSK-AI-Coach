# ============================================================
# tests/test_visitor_gate.py
# P0.10 · 游客模式闸门（每日配额，BYOK 无限）
# 覆盖：
#   A. VisitorGate 纯函数：ensure_vid cookie 提取/生成、check_and_consume 计次/满额拒/reset、
#      daily_quota=0 关闭、跨天自动重置（mock _utc_today）
#   B. HTTP 集成：make_handler 注入 gate——首访下发 Set-Cookie、带 cookie 复用同 vid、
#      满额 429 且模型未被调用/次数未增、BYOK（provider_id!=env）完全豁免、放行时返回 200
# 对齐验收：游客第 11 次被 429（模型未调、次数未增）；带自有 Key 豁免；跨天重置（UTC）。
# 运行: python -m unittest tests.test_visitor_gate -v
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

from engine.graph.error_graph import ErrorGraph
from engine.serve import make_handler
from engine.visitor_gate import COOKIE_NAME, VisitorGate, _utc_today


# ---------------- 替身 ----------------

class FakeRecognizer:
    def recognize(self, text, level=3, native_lang=""):
        return {"errors": [], "uncertain": [], "degraded": []}


class FakeRouter:
    learner_id = "visitor_user"

    def __init__(self):
        self.graph = ErrorGraph("visitor_test")
        self.recognizer = FakeRecognizer()
        self.verifier = None
        self.explainer = None


class CountingLLM:
    """脚本化 llm_call：直答文本收尾；统计被调用次数（验证满额时模型未被打）。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, messages):
        self.calls += 1
        return '[{"type":"text","content":"好的。"}]'


def _start_server(router, dialog_llm=None, gate=None, tmp=None):
    tmp = tmp or tempfile.mkdtemp()
    if not os.path.isfile(os.path.join(tmp, "index.html")):
        with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
            f.write("<html>visitor</html>")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", port),
        make_handler(router, tmp, dialog_llm=dialog_llm, memory_root=tmp, gate=gate))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _post(port, path, body, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    c.request("POST", path, json.dumps(body).encode("utf-8"), headers)
    r = c.getresponse()
    raw = r.read()
    set_cookie = r.getheader("Set-Cookie")
    c.close()
    try:
        return r.status, json.loads(raw.decode()), set_cookie
    except Exception:  # noqa: BLE001
        return r.status, None, set_cookie


def _vid_from(set_cookie):
    """从 Set-Cookie 头里取 hsk_vid 值。"""
    if not set_cookie:
        return None
    for part in set_cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE_NAME and v:
            return v
    return None


# ---------------- A · 纯函数 ----------------

class VisitorGateUnitTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_check_and_consume_release_then_block(self):
        g = VisitorGate(self.root, daily_quota=2)
        ok1, r1, _ = g.check_and_consume("a")
        self.assertTrue(ok1), self.assertEqual(r1, 1)
        ok2, r2, _ = g.check_and_consume("a")
        self.assertTrue(ok2), self.assertEqual(r2, 0)
        ok3, r3, reset = g.check_and_consume("a")
        self.assertFalse(ok3), self.assertEqual(r3, 0)
        self.assertIn("重置", reset)

    def test_cross_day_reset(self):
        g = VisitorGate(self.root, daily_quota=1)
        g.check_and_consume("b")
        ok, _, _ = g.check_and_consume("b")
        self.assertFalse(ok)                 # 同日超限
        with mock.patch("engine.visitor_gate._utc_today",
                        return_value="2099-01-02"):
            ok2, r, _ = g.check_and_consume("b")
            self.assertTrue(ok2)             # 跨天自动重置
            self.assertEqual(r, 0)

    def test_quota_zero_disables(self):
        g = VisitorGate(self.root, daily_quota=0)
        ok, remaining, _ = g.check_and_consume("c")
        self.assertTrue(ok), self.assertEqual(remaining, -1)

    def test_per_sid_independent(self):
        g = VisitorGate(self.root, daily_quota=1)
        g.check_and_consume("x")
        ok, _, _ = g.check_and_consume("y")   # 不同 vid 互不影响
        self.assertTrue(ok)

    def test_persist_roundtrip(self):
        g1 = VisitorGate(self.root, daily_quota=5)
        g1.check_and_consume("persist")
        g2 = VisitorGate(self.root, daily_quota=5)   # 重载读盘
        self.assertEqual(g2._data.get("persist", {}).get("count"), 1)

    def test_utc_today_format(self):
        self.assertEqual(len(_utc_today()), 10)      # YYYY-MM-DD
        self.assertEqual(_utc_today()[4], "-")


# ---------------- B · HTTP 集成 ----------------

class VisitorGateHTTPTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.router = FakeRouter()
        self.llm = CountingLLM()
        self.gate = VisitorGate(self.tmp, daily_quota=2)
        self.httpd, self.port = _start_server(
            self.router, dialog_llm=self.llm, gate=self.gate, tmp=self.tmp)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _dialog(self, provider="", cookie=None):
        body = {"text": "你好", "learner_id": "u1"}
        if provider:
            body["provider_id"] = provider
        return _post(self.port, "/api/dialog", body, cookie)

    def test_first_visit_sets_cookie_and_counts(self):
        st, out, sc = self._dialog()
        self.assertEqual(st, 200)
        vid = _vid_from(sc)
        self.assertIsNotNone(vid)                      # 首次下发 vid
        self.assertEqual(self.gate._data[vid]["count"], 1)

    def test_cookie_reuses_same_vid(self):
        st1, _, sc1 = self._dialog()
        vid = _vid_from(sc1)
        st2, _, sc2 = self._dialog(cookie=f"{COOKIE_NAME}={vid}")
        self.assertEqual(st2, 200)
        self.assertIsNone(_vid_from(sc2))              # 已有 vid 不再下发
        self.assertEqual(self.gate._data[vid]["count"], 2)

    def test_quota_exhausted_429_no_model_call_no_increment(self):
        st1, _, sc1 = self._dialog()
        vid = _vid_from(sc1)
        st2, _, _ = self._dialog(cookie=f"{COOKIE_NAME}={vid}")
        self.assertEqual(st2, 200)
        calls_before = self.llm.calls
        st3, out, _ = self._dialog(cookie=f"{COOKIE_NAME}={vid}")
        self.assertEqual(st3, 429)
        self.assertEqual(out["code"], "visitor_quota_exceeded")
        self.assertEqual(out["remaining"], 0)
        self.assertEqual(self.llm.calls, calls_before)  # 模型未被调用
        self.assertEqual(self.gate._data[vid]["count"], 2)  # 次数未增

    def test_owner_env_key_uses_quota(self):
        from engine import providers as prov
        st, _, sc = self._dialog(provider=prov.ENV_PROVIDER_ID)
        self.assertEqual(st, 200)
        vid = _vid_from(sc)
        self.assertEqual(self.gate._data[vid]["count"], 1)  # env id 仍计游客配额

    def test_byok_fully_exempt(self):
        # 先用 owner 路径耗尽配额
        st1, _, sc = self._dialog()
        vid = _vid_from(sc)
        self._dialog(cookie=f"{COOKIE_NAME}={vid}")
        # BYOK（provider_id=自定义非 env）→ 完全豁免，即使配额已尽
        st, out, _ = self._dialog(provider="myprov", cookie=f"{COOKIE_NAME}={vid}")
        self.assertEqual(st, 200)
        self.assertEqual(self.gate._data[vid]["count"], 2)  # 未额外计游客配额

    def test_cross_day_reset_over_http(self):
        st1, _, sc = self._dialog()
        vid = _vid_from(sc)
        self._dialog(cookie=f"{COOKIE_NAME}={vid}")
        with mock.patch("engine.visitor_gate._utc_today",
                        return_value="2099-03-03"):
            st, out, _ = self._dialog(cookie=f"{COOKIE_NAME}={vid}")
            self.assertEqual(st, 200)          # 新一天重置，放行
            self.assertEqual(self.gate._data[vid]["count"], 1)


if __name__ == "__main__":
    unittest.main()
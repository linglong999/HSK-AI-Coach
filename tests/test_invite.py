# ============================================================
# tests/test_invite.py
# B7 S5 · 0→1 邀请制 allowlist
# 覆盖：
#   A. 纯函数：parse_allowlist 清洗（strip/去空/去重）、is_open、check_openid
#   B. HTTP：白名单开启后——未带 open_id 访问页面→邀请页(200不暴露壳)；
#      带未邀请 open_id→邀请页；带已邀请 open_id→正常壳；未授权 /api/*→403
#   C. 默认（白名单空）→ 守卫完全旁路（既有契约不变，不影响其它 serve 测试）
# 对齐验收 ⑤：allowlist 拒绝未邀请 open_id。
# 运行: python -m unittest tests.test_invite -v
# ============================================================

import http.client
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.invite import (OPENID_COOKIE, check_openid, is_open,
                           parse_allowlist)
from tests._serve_common import make_server, stop_server
from engine.serve import create_app


# ---------------- 替身 ----------------

class Rec:
    def recognize(self, text, level=3, native_lang=""):
        return {"errors": [], "uncertain": [], "degraded": []}


class Router:
    learner_id = "invite_user"
    native_lang = "zh"

    def __init__(self):
        from engine.graph.error_graph import ErrorGraph
        self.graph = ErrorGraph("invite_test")
        self.recognizer = Rec()


# ---------------- A · 纯函数 ----------------

class InviteFuncTest(unittest.TestCase):
    def test_parse_allowlist_cleans(self):
        self.assertEqual(parse_allowlist(""), set())
        self.assertEqual(parse_allowlist(None), set())
        self.assertEqual(parse_allowlist(" a , b , a ,, c "),
                         {"a", "b", "c"})      # strip + 去重 + 去空
        self.assertEqual(parse_allowlist("abc"), {"abc"})

    def test_is_open(self):
        self.assertTrue(is_open(set()))
        self.assertTrue(is_open(None))
        self.assertFalse(is_open({"a"}))

    def test_check_openid(self):
        al = {"a", "b"}
        self.assertFalse(check_openid(al, None))
        self.assertFalse(check_openid(al, ""))
        self.assertFalse(check_openid(al, "x"))      # 未邀请
        self.assertTrue(check_openid(al, "a"))       # 命中
        self.assertTrue(check_openid("a", "a"))      # None 允许等价空集→开放
        self.assertTrue(check_openid(set(), "any"))  # 闸门关闭恒放行


# ---------------- B/C · HTTP 拦截 ----------------

def _get(port, path, cookie=None, open_id=None):
    qs = f"?open_id={open_id}" if open_id else ""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Cookie": cookie} if cookie else {}
    c.request("GET", path + qs, headers=h)
    r = c.getresponse()
    raw = r.read()
    st, ct = r.status, r.getheader("Content-Type") or ""
    c.close()
    return st, raw.decode(), ct


def _post(port, path, body, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Content-Type": "application/json"}
    if cookie:
        h["Cookie"] = cookie
    c.request("POST", path, json.dumps(body).encode("utf-8"), h)
    r = c.getresponse()
    raw = r.read()
    st = r.status
    c.close()
    return st, json.loads(raw.decode())


def _build_app(allowlist):
    """在指定白名单下构造 app（守卫在 create_app 时快照 settings）。"""
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html>legacy-authorized</html>")
    with mock.patch("config.settings.INVITE_ALLOWLIST", allowlist):
        return create_app(Router(), tmp, memory_root=tmp, gate=None), tmp


class InviteHTTPBase(unittest.TestCase):
    def setUp(self):
        self.server, self.thr, self.port, self.tmp = self._make()

    def tearDown(self):
        stop_server(self.server, self.thr)

    def _make(self):
        raise NotImplementedError


class InviteClosedTest(InviteHTTPBase):
    """C · 默认（白名单空）守卫旁路。"""
    def _make(self):
        app, tmp = _build_app("")
        s, t, p = make_server(app)
        return s, t, p, tmp

    def test_access_page_normal(self):
        st, body, ct = _get(self.port, "/")
        self.assertEqual(st, 200)
        self.assertIn("legacy-authorized", body)   # 正常出壳，非邀请页

    def test_access_api_normal(self):
        st, _, _ = _get(self.port, "/api/graph")
        # 路由可达且非邀请拦截（403 invite_required）即证明守卫旁路
        self.assertNotEqual(st, 403)


class InviteOnTest(InviteHTTPBase):
    """B · 白名单开启后拦截。"""
    ALLOW = "invite-alice, invoke-bob"

    def _make(self):
        app, tmp = _build_app(self.ALLOW)
        s, t, p = make_server(app)
        return s, t, p, tmp

    def test_missing_openid_shows_invite_page(self):
        st, body, ct = _get(self.port, "/")
        self.assertEqual(st, 200)
        self.assertIn("邀请制内测", body)          # 只出邀请页
        self.assertNotIn("legacy-authorized", body)  # 不暴露壳

    def test_uninvited_openid_shows_invite_page(self):
        st, body, _ = _get(self.port, "/", open_id="stranger")
        self.assertEqual(st, 200)
        self.assertIn("邀请制内测", body)

    def test_invited_openid_gets_shell(self):
        st, body, _ = _get(self.port, "/", open_id="invite-alice")
        self.assertEqual(st, 200)
        self.assertIn("legacy-authorized", body)

    def test_invited_via_cookie_gets_shell(self):
        st, body, _ = _get(self.port, "/",
                           cookie=f"{OPENID_COOKIE}=invoke-bob")
        self.assertEqual(st, 200)
        self.assertIn("legacy-authorized", body)

    def test_uninvited_api_403(self):
        st, out = _post(self.port, "/api/process",
                        {"text": "你好", "native_lang": "zh"})
        self.assertEqual(st, 403)
        self.assertEqual(out["error"], "invite_required")

    def test_invited_api_passes(self):
        st, _, _ = _get(self.port, "/api/graph",
                        cookie=f"{OPENID_COOKIE}=invite-alice")
        self.assertNotEqual(st, 403)


if __name__ == "__main__":
    unittest.main()
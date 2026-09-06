# ============================================================
# tests/test_level.py
# 0.25 起点分层回归测试
# 覆盖（对照"identify 漏传 level=恒3"教训，必须 planner 级+引擎实收双锁定）：
#   - Router._level_int / serve._normalize_user_level：归一（'HSK3'/'3'/3/非法/边界）
#   - serve：POST /api/profile 保存 user_level → GET 带回；非法 400
#   - dialog 主链：画像等级 → 预扫识别引擎实收 level + planner trace.params 注入 level/user_level；
#     未保存等级 → 回落 HSK3（默认不注入额外约束）
# 运行: python -m unittest tests.test_level -v
# ============================================================

import copy
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.graph.error_graph import ErrorGraph
from engine.router import Router
from engine.serve import make_handler


# ---------------- 单元：Router._level_int + serve._normalize_user_level ----------------

class LevelNormalizeTest(unittest.TestCase):

    def test_router_level_int(self):
        self.assertEqual(Router(user_level="HSK1")._level_int(), 1)
        self.assertEqual(Router(user_level="HSK6")._level_int(), 6)
        self.assertEqual(Router(user_level="3")._level_int(), 3)
        self.assertEqual(Router(user_level=4)._level_int(), 4)
        self.assertEqual(Router(user_level="HSK9")._level_int(), 6)   # 越界钳到 6
        self.assertEqual(Router(user_level="HSK0")._level_int(), 1)   # 越界钳到 1
        self.assertEqual(Router(user_level="xxx")._level_int(), 3)    # 非法回退

    def test_serve_normalize_user_level(self):
        # 0.25 起点分层：serve 自身静态归一（'HSK1'-'HSK6'/数字/非法）语义锁定。
        # 直接测 handler 静态方法，避免"只测 Router 却以 serve 命名"的假绿。
        import tempfile
        from engine.serve import make_handler as _m
        tmp = tempfile.mkdtemp()
        router = make_fake_router("lvl_unit")
        H = _m(router, tmp, dialog_llm=None)
        self.assertEqual(H._normalize_user_level("HSK1"), "HSK1")
        self.assertEqual(H._normalize_user_level("hsk6"), "HSK6")
        self.assertEqual(H._normalize_user_level(4), "HSK4")
        self.assertEqual(H._normalize_user_level("HSK0"), None)   # 越界非法
        self.assertEqual(H._normalize_user_level("HSK9"), None)
        self.assertEqual(H._normalize_user_level(0), None)
        self.assertEqual(H._normalize_user_level("xxx"), None)
        self.assertEqual(H._normalize_user_level(None), None)


# ---------------- serve 集成：保存 / 校验 / dialog 注入 ----------------

TEXT_REPLY = '[{"type":"text","content":"%s"}]'


class FakeRecognizer:
    def __init__(self):
        self.last_level = None

    def recognize(self, text, level=3, native_lang=""):
        self.last_level = level
        return {"errors": [], "uncertain": [], "degraded": []}


class CaptureLLM:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def __call__(self, messages):
        self.calls.append(copy.deepcopy(messages))
        if self.responses:
            return self.responses.pop(0)
        return TEXT_REPLY % "好的。"

    def reset(self, responses):
        self.responses = list(responses)
        self.calls.clear()


def make_fake_router(learner_id):
    class FakeRouter:
        pass
    r = FakeRouter()
    r.learner_id = learner_id
    r.graph = ErrorGraph(f"dialog_{learner_id}")
    r.recognizer = FakeRecognizer()
    r.verifier = None
    r.explainer = None
    return r


def _start_server(router, dialog_llm):
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html>lvl</html>")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", port),
        make_handler(router, tmp, dialog_llm=dialog_llm, memory_root=tmp))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port, tmp


def _post(port, path, body):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", path, json.dumps(body).encode(),
              {"Content-Type": "application/json"})
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw.decode())
    except Exception:
        return r.status, None


def _get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw.decode())
    except Exception:
        return r.status, None


def _cleanup_graph(learner_id):
    p = os.path.join(_PROJECT_ROOT, "data", f"graph_dialog_{learner_id}.json")
    if os.path.exists(p):
        os.remove(p)


class ServeLevelTest(unittest.TestCase):

    LEARNER = "lvl_t"

    @classmethod
    def setUpClass(cls):
        cls.router = make_fake_router(cls.LEARNER)
        cls.llm = CaptureLLM()
        cls.httpd, cls.port, cls.root = _start_server(cls.router, cls.llm)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        _cleanup_graph(cls.LEARNER)

    def _save_level(self, raw, learner=None):
        body = {"user_level": raw, "learner": learner or self.LEARNER}
        return _post(self.port, "/api/profile", body)

    def test_save_roundtrip_normalizes(self):
        st, out = self._save_level("HSK1")
        self.assertEqual(st, 200)
        self.assertEqual(out["user_level"], "HSK1")
        st, prof = _get(self.port, "/api/profile?learner=" + self.LEARNER)
        self.assertEqual(st, 200)
        self.assertEqual(prof["profile"]["user_level"], "HSK1")

    def test_save_numeric_and_lowercase(self):
        st, out = self._save_level(4)
        self.assertEqual(st, 200)
        self.assertEqual(out["user_level"], "HSK4")
        st, out = self._save_level("hsk6")
        self.assertEqual(st, 200)
        self.assertEqual(out["user_level"], "HSK6")

    def test_invalid_user_level_400(self):
        st, out = self._save_level("HSK9")
        self.assertEqual(st, 400)
        self.assertEqual(out.get("code"), "invalid_user_level")
        st, out = self._save_level(0)
        self.assertEqual(st, 400)

    def test_dialog_injects_level_to_recognize_and_planner(self):
        # 保存 HSK1 后：预扫识别引擎实收 level=1，planner trace params.level="HSK1"
        st, _ = self._save_level("HSK1")
        self.assertEqual(st, 200)
        self.llm.reset([
            json.dumps([{"type": "action", "name": "identify_errors",
                         "params": {"text": "苹果很多"}}], ensure_ascii=False),
            TEXT_REPLY % "检查完毕。",
        ])
        # 先清掉预扫的实收（预扫也走同识别器）
        router_rec = self.router.recognizer
        router_rec.last_level = None
        st, out = _post(self.port, "/api/dialog", {
            "text": "苹果很多", "learner_id": self.LEARNER,
            "conversation_id": "conv-lvl1"})
        self.assertEqual(st, 200)
        # 引擎实收（serve 预扫传 level_label → skill normalizse 为 int；可能有预扫+planner 两次）
        self.assertEqual(router_rec.last_level, 1)
        # planner trace.params 注入（0.25 教训：必须 planner 级锁定，技能级测不到静默失效）
        t = next(t for t in out.get("trace", [])
                 if t.get("name") == "identify_errors")
        self.assertEqual(t["params"].get("level"), "HSK1")

    def test_dialog_default_level_when_unset(self):
        # 未保存等级 → 回落默认 HSK3（预扫识别实收 level=3）
        fresh = self.LEARNER + "_fresh"
        fresh_router = make_fake_router(fresh)
        httpd, port, _ = _start_server(fresh_router, CaptureLLM([TEXT_REPLY % "噢。"]))
        try:
            st, out = _post(port, "/api/dialog", {
                "text": "你好", "learner_id": fresh, "conversation_id": "conv-d"})
            self.assertEqual(st, 200)
            self.assertEqual(fresh_router.recognizer.last_level, 3)
        finally:
            httpd.shutdown()
            httpd.server_close()
            _cleanup_graph(fresh)


if __name__ == "__main__":
    unittest.main(verbosity=2)
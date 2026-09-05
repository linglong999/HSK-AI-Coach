# ============================================================
# POST /api/session/update + /api/session/delete 端到端测试（0.24 会话右键菜单）
# 覆盖：
#   - 置顶：pinned 标记落盘 + profile 排序置顶优先；取消置顶恢复
#   - 重命名：title 更新；空标题 400；超长截 60；未知会话 404
#   - 校验：缺 conversation_id 400 / 空字段 400 / 非 bool pinned 400
#   - 删除：会话从 profile 消失、conversation 端点变空；二次删除 404
#   - 存储层：touch(pinned, bump=False) 不动 updated_at；delete_session 往返
# 运行: python -m unittest tests.test_serve_sessions -v
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
from engine.memory.learner_memory import LearnerMemory
from engine.serve import make_handler


# ---------------- 测试替身（对齐 test_serve_profile） ----------------

class FakeRecognizer:
    def recognize(self, text, level=3, native_lang=""):
        return {
            "errors": [{
                "fragment": "苹果很多", "correction": "很多苹果",
                "type": "语法-语序", "type_confident": True, "confidence": 0.9,
                "knowledge_point_id": "kp-order-many", "uncertain": False,
            }],
            "uncertain": [], "degraded": [],
        }


class FakeRouter:
    learner_id = "sess_user"

    def __init__(self):
        self.graph = ErrorGraph("sess_graph_test")
        self.recognizer = FakeRecognizer()
        self.verifier = None
        self.explainer = None


class ScriptLLM:
    def __init__(self, responses=None):
        self.responses = list(responses or [])

    def __call__(self, messages):
        if self.responses:
            return self.responses.pop(0)
        return '[{"type":"text","content":"好的。"}]'


def _start_server(router, dialog_llm=None):
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html>sess</html>")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", port),
        make_handler(router, tmp, dialog_llm=dialog_llm, memory_root=tmp))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


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


_DIALOG_SCRIPT = [
    '[{"type":"action","name":"identify_errors",'
    '  "params":{"text":"我想买苹果很多。"}}]',
    '[{"type":"text","content":"已检查这句话。"}]',
]


class SessionManageTest(unittest.TestCase):
    """session update/delete 两端点 × profile 排序联动。"""

    @classmethod
    def setUpClass(cls):
        cls.router = FakeRouter()
        cls.llm = ScriptLLM()
        cls.httpd, cls.port = _start_server(cls.router, dialog_llm=cls.llm)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        p = os.path.join(_PROJECT_ROOT, "data", "graph_sess_graph_test.json")
        if os.path.exists(p):
            os.remove(p)

    def setUp(self):
        self.llm.responses = list(_DIALOG_SCRIPT)

    def _dialog(self, conversation_id):
        return _post(self.port, "/api/dialog",
                     {"text": "我想买苹果很多。", "conversation_id": conversation_id})

    def _sess(self, profile, sid):
        return next((s for s in profile["sessions"] if s["id"] == sid), None)

    # ---------------- 置顶 ----------------

    def test_pin_moves_session_to_top(self):
        self._dialog("s-old")
        self._dialog("s-new")
        st, _ = _post(self.port, "/api/session/update",
                      {"conversation_id": "s-old", "pinned": True})
        self.assertEqual(st, 200)
        st, prof = _get(self.port, "/api/profile")
        self.assertEqual(st, 200)
        ids = [s["id"] for s in prof["sessions"]]
        self.assertEqual(ids[0], "s-old")          # 置顶项跳到首位
        self.assertLess(ids.index("s-old"), ids.index("s-new"))
        self.assertTrue(self._sess(prof, "s-old")["pinned"])
        self.assertFalse(self._sess(prof, "s-new")["pinned"])

    def test_unpin_restores_flag(self):
        _post(self.port, "/api/session/update",
              {"conversation_id": "s-old", "pinned": True})
        st, out = _post(self.port, "/api/session/update",
                        {"conversation_id": "s-old", "pinned": False})
        self.assertEqual(st, 200)
        st, prof = _get(self.port, "/api/profile")
        self.assertFalse(self._sess(prof, "s-old")["pinned"])

    # ---------------- 重命名 ----------------

    def test_rename_updates_title(self):
        st, out = _post(self.port, "/api/session/update",
                        {"conversation_id": "s-new", "title": "苹果语序课"})
        self.assertEqual(st, 200)
        self.assertEqual(out["title"], "苹果语序课")
        st, prof = _get(self.port, "/api/profile")
        self.assertEqual(self._sess(prof, "s-new")["title"], "苹果语序课")

    def test_rename_empty_title_400(self):
        st, out = _post(self.port, "/api/session/update",
                        {"conversation_id": "s-new", "title": "   "})
        self.assertEqual(st, 400)
        self.assertEqual(out["code"], "invalid_title")

    def test_rename_long_title_truncated_to_60(self):
        st, out = _post(self.port, "/api/session/update",
                        {"conversation_id": "s-new", "title": "长" * 80})
        self.assertEqual(st, 200)
        self.assertEqual(len(out["title"]), 60)

    # ---------------- 校验与 404 ----------------

    def test_update_missing_conversation_id_400(self):
        st, out = _post(self.port, "/api/session/update", {"pinned": True})
        self.assertEqual(st, 400)
        self.assertEqual(out["code"], "missing_conversation_id")

    def test_update_nothing_to_update_400(self):
        st, out = _post(self.port, "/api/session/update",
                        {"conversation_id": "s-new"})
        self.assertEqual(st, 400)
        self.assertEqual(out["code"], "nothing_to_update")

    def test_update_pinned_not_bool_400(self):
        st, out = _post(self.port, "/api/session/update",
                        {"conversation_id": "s-new", "pinned": "yes"})
        self.assertEqual(st, 400)
        self.assertEqual(out["code"], "invalid_pinned")

    def test_update_unknown_session_404(self):
        st, out = _post(self.port, "/api/session/update",
                        {"conversation_id": "s-ghost", "pinned": True})
        self.assertEqual(st, 404)
        self.assertEqual(out["code"], "session_not_found")

    # ---------------- 删除 ----------------

    def test_delete_removes_session(self):
        self._dialog("s-doom")
        st, out = _post(self.port, "/api/session/delete",
                        {"conversation_id": "s-doom"})
        self.assertEqual(st, 200)
        st, prof = _get(self.port, "/api/profile")
        self.assertIsNone(self._sess(prof, "s-doom"))
        st, conv = _get(self.port,
                        "/api/conversation?id=s-doom")
        self.assertEqual(st, 200)
        self.assertEqual(conv["messages"], [])

    def test_delete_twice_404(self):
        self._dialog("s-once")
        _post(self.port, "/api/session/delete", {"conversation_id": "s-once"})
        st, out = _post(self.port, "/api/session/delete",
                        {"conversation_id": "s-once"})
        self.assertEqual(st, 404)
        self.assertEqual(out["code"], "session_not_found")

    def test_delete_unknown_404(self):
        st, _ = _post(self.port, "/api/session/delete",
                      {"conversation_id": "s-ghost"})
        self.assertEqual(st, 404)


class LearnerMemoryPinDeleteTest(unittest.TestCase):
    """存储层契约：pinned/bump=False 语义 + delete_session 往返。"""

    def _mem(self):
        return LearnerMemory("sess_unit", root=tempfile.mkdtemp())

    def test_touch_pinned_does_not_bump_updated_at(self):
        mem = self._mem()
        mod = sys.modules["engine.memory.learner_memory"]
        with mock.patch.object(mod.time, "time", return_value=1000):
            mem.append("s1", "user", "你好")
        with mock.patch.object(mod.time, "time", return_value=2000):
            mem.touch("s1", pinned=True, bump=False)
        raw = mem._load()["sessions"]["s1"]
        self.assertEqual(raw["updated_at"], 1000)   # 管理操作不动活跃度
        self.assertTrue(raw["pinned"])
        # 落盘往返：reload 后仍在
        mem.reload()
        raw = mem._load()["sessions"]["s1"]
        self.assertTrue(raw.get("pinned"))

    def test_touch_bump_default_still_works(self):
        mem = self._mem()
        mod = sys.modules["engine.memory.learner_memory"]
        with mock.patch.object(mod.time, "time", return_value=1000):
            mem.append("s1", "user", "你好")
        with mock.patch.object(mod.time, "time", return_value=3000):
            mem.touch("s1", title="标题")
        raw = mem._load()["sessions"]["s1"]
        self.assertEqual(raw["updated_at"], 3000)   # 默认 bump 语义保留

    def test_delete_session_roundtrip(self):
        mem = self._mem()
        mem.append("s1", "user", "你好")
        mem.append("s2", "user", "再见")
        self.assertTrue(mem.delete_session("s1"))
        self.assertEqual(mem.get_history("s1"), [])  # 历史随会话消失
        self.assertEqual(len(mem.get_history("s2", window=10)), 1)
        self.assertFalse(mem.delete_session("s1"))   # 二次删除 → False
        mem.reload()
        self.assertIn("s2", mem._load()["sessions"])
        self.assertNotIn("s1", mem._load()["sessions"])

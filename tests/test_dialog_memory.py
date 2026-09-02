# ============================================================
# /api/dialog × M5 多轮记忆 集成测试（0.18 接入项①）
# 覆盖（agent-design M5 验收标准）：
#   - 落盘：一轮对话后 memory_<learner>.json 有 user+assistant 两条
#   - 多轮沿用：第二轮 planner 收到的 messages 含第一轮上下文（"那区别呢"场景）
#   - 会话隔离：切换 conversation_id 后不注入旧会话历史
#   - 兜底：不传 conversation_id → "default"
#   - fallback 写回：降级文案也落盘，多轮不断档
#   - 无 Key 400 时不产生记忆文件（fail-loud 在读记忆之前）
# 运行: python -m unittest tests.test_dialog_memory -v
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
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.graph.error_graph import ErrorGraph
from engine.serve import make_handler


# ---------------- 测试替身 ----------------

class FakeRecognizer:
    """固定返回一个已确认偏误（免 LLM、确定性）。"""

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
    """最小 Router 替身：learner_id + 共享图谱 + 假识别引擎。"""

    learner_id = "mem_user"

    def __init__(self):
        self.graph = ErrorGraph("dialog_mem_test")
        self.recognizer = FakeRecognizer()
        self.verifier = None
        self.explainer = None


class CaptureLLM:
    """脚本化 llm_call：记录每次收到的 messages（深拷贝），按序返回响应。"""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def __call__(self, messages):
        self.calls.append(copy.deepcopy(messages))
        if self.responses:
            return self.responses.pop(0)
        return '[{"type":"text","content":"好的。"}]'

    def reset(self, responses):
        self.responses = list(responses or [])
        self.calls.clear()


def _start_server(router, dialog_llm=None, memory_root=None):
    tmp = tempfile.mkdtemp()
    if memory_root is None:
        memory_root = tmp
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html>mem</html>")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", port),
        make_handler(router, tmp, dialog_llm=dialog_llm, memory_root=memory_root))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port, memory_root


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


TEXT_REPLY = '[{"type":"text","content":"%s"}]'


# ---------------- 集成用例 ----------------

class DialogMemoryIntegrationTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.router = FakeRouter()
        cls.llm = CaptureLLM()
        cls.httpd, cls.port, cls.mem_root = _start_server(
            cls.router, dialog_llm=cls.llm)
        cls.learner = "mem_user"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        p = os.path.join(_PROJECT_ROOT, "data", "graph_dialog_mem_test.json")
        if os.path.exists(p):
            os.remove(p)

    def _mem_path(self, learner=None):
        from engine.memory.learner_memory import LearnerMemory
        lid = learner or self.learner
        inst = LearnerMemory(lid, root=self.mem_root)
        return inst._path

    def _load_mem(self, learner=None):
        with open(self._mem_path(learner), encoding="utf-8") as f:
            return json.load(f)

    def test_first_round_persists_and_echoes_conversation_id(self):
        self.llm.reset([TEXT_REPLY % "第一轮回复。"])
        st, out = _post(self.port, "/api/dialog", {
            "text": "我想买苹果很多。", "learner_id": self.learner,
            "conversation_id": "conv-A"})
        self.assertEqual(st, 200)
        self.assertEqual(out.get("conversation_id"), "conv-A")
        data = self._load_mem()
        self.assertEqual(data.get("learner_id"), self.learner)
        msgs = data["sessions"]["conv-A"]["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[0]["content"], "我想买苹果很多。")
        self.assertEqual(msgs[1]["content"], "第一轮回复。")
        self.assertEqual(msgs[1]["metadata"].get("skills"), [])
        self.assertFalse(msgs[1]["metadata"].get("fallback"))

    def test_second_round_injects_memory_history(self):
        # 第一轮
        self.llm.reset([TEXT_REPLY % "量词要放在名词前面。"])
        _post(self.port, "/api/dialog", {
            "text": "苹果很多为什么错？", "learner_id": self.learner,
            "conversation_id": "conv-B"})
        # 第二轮追问（服务端记忆应注入第一轮上下文）
        self.llm.reset([TEXT_REPLY % "接着上一轮讲。"])
        st, out = _post(self.port, "/api/dialog", {
            "text": "那区别呢", "learner_id": self.learner,
            "conversation_id": "conv-B"})
        self.assertEqual(st, 200)
        messages = self.llm.calls[0]
        contents = [(m["role"], m["content"]) for m in messages]
        self.assertIn(("user", "苹果很多为什么错？"), contents)
        self.assertIn(("assistant", "量词要放在名词前面。"), contents)
        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(messages[-1]["content"], "那区别呢")
        # 前端透传的 history 被忽略（后端记忆为唯一权威源）
        st, out = _post(self.port, "/api/dialog", {
            "text": "继续", "learner_id": self.learner,
            "conversation_id": "conv-B",
            "history": [{"role": "user", "content": "伪造历史"}]})
        self.assertEqual(st, 200)
        contents = [(m["role"], m["content"]) for m in self.llm.calls[0]]
        self.assertNotIn(("user", "伪造历史"), contents)

    def test_conversation_switch_isolated(self):
        self.llm.reset([TEXT_REPLY % "会话一的内容。"])
        _post(self.port, "/api/dialog", {
            "text": "会话一第一句", "learner_id": self.learner,
            "conversation_id": "conv-C1"})
        self.llm.reset([TEXT_REPLY % "会话二的回复。"])
        st, out = _post(self.port, "/api/dialog", {
            "text": "会话二第一句", "learner_id": self.learner,
            "conversation_id": "conv-C2"})
        self.assertEqual(st, 200)
        contents = [(m["role"], m["content"]) for m in self.llm.calls[0]]
        self.assertNotIn(("user", "会话一第一句"), contents)
        self.assertNotIn(("assistant", "会话一的内容。"), contents)
        self.assertEqual(self.llm.calls[0][-1]["role"], "user")
        self.assertEqual(self.llm.calls[0][-1]["content"], "会话二第一句")

    def test_default_conversation_when_missing(self):
        self.llm.reset([TEXT_REPLY % "默认会话回复。"])
        st, out = _post(self.port, "/api/dialog", {
            "text": "不传会话 id", "learner_id": self.learner})
        self.assertEqual(st, 200)
        self.assertEqual(out.get("conversation_id"), "default")
        data = self._load_mem()
        self.assertIn("default", data["sessions"])

    def test_fallback_reply_persisted_and_continues(self):
        # 第一轮：LLM 输出非 JSON（重试一次仍坏，"这不是JSON" 过短不入裸文本
        # 兜底）→ parse fallback 文案落盘
        self.llm.reset(["这不是JSON", "这不是JSON"])
        st, out = _post(self.port, "/api/dialog", {
            "text": "帮我看看这句", "learner_id": self.learner,
            "conversation_id": "conv-D"})
        self.assertEqual(st, 200)
        self.assertTrue(out.get("fallback"))
        data = self._load_mem()
        msgs = data["sessions"]["conv-D"]["messages"]
        self.assertEqual(msgs[-1]["role"], "assistant")
        self.assertTrue(msgs[-1]["metadata"].get("fallback"))
        self.assertEqual(msgs[-1]["content"], out.get("text"))
        # 第二轮：fallback 文案作为历史注入，多轮不断档
        self.llm.reset([TEXT_REPLY % "恢复正常。"])
        st, out2 = _post(self.port, "/api/dialog", {
            "text": "换个说法", "learner_id": self.learner,
            "conversation_id": "conv-D"})
        self.assertEqual(st, 200)
        contents = [(m["role"], m["content"]) for m in self.llm.calls[0]]
        self.assertIn(("assistant", out.get("text")), contents)


# ---------------- 无 Key：fail-loud 且不产生记忆文件 ----------------

class NoKeyNoMemoryWriteTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.router = FakeRouter()
        cls.httpd, cls.port, cls.mem_root = _start_server(cls.router)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_400_and_no_memory_file(self):
        with mock.patch("config.settings.DEEPSEEK_API_KEY", ""), \
             mock.patch("config.settings.LLM_PROVIDER", "deepseek"):
            st, out = _post(self.port, "/api/dialog", {
                "text": "我想买苹果很多。", "learner_id": "nokey_user",
                "conversation_id": "conv-X"})
        self.assertEqual(st, 400)
        self.assertEqual(out.get("code"), "llm_not_configured")
        files = [f for f in os.listdir(self.mem_root) if f.startswith("memory_")]
        self.assertEqual(files, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

# ============================================================
# GET /api/profile + GET /api/conversation 端到端测试（0.19 前端 v2）
# 覆盖：
#   - profile 契约：learner_id/profile/common_errors/summary_text/sessions/stats
#   - stats 计数：dialog 一次（identify 命中）→ graph_nodes=1、review_count>=1、惯犯=[]
#   - 会话元信息：首条消息自动设标题（前 18 字）、message_count、updated_at 降序
#   - conversation 深链恢复：user/assistant 按序、仅 {role,content}（不泄漏 metadata）
#   - 未知会话 → 空 messages；多轮 → 消息按序追加
# 运行: python -m unittest tests.test_serve_profile -v
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

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.graph.error_graph import ErrorGraph
from engine.serve import make_handler


# ---------------- 测试替身（对齐 test_serve_dialog） ----------------

class FakeRecognizer:
    """固定返回一个已确认偏误（uncertain=false + kp 命中 → 直写图谱节点）。"""

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
    learner_id = "profile_user"

    def __init__(self):
        self.graph = ErrorGraph("profile_test")
        self.recognizer = FakeRecognizer()
        self.verifier = None
        self.explainer = None


class ScriptLLM:
    """脚本化 llm_call：按序返回；耗尽后固定 text 收尾。"""

    def __init__(self, responses=None):
        self.responses = list(responses or [])

    def __call__(self, messages):
        if self.responses:
            return self.responses.pop(0)
        return '[{"type":"text","content":"好的。"}]'


def _start_server(router, dialog_llm=None):
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html>profile</html>")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    # memory_root=tmp：memory/ledger 落盘不污染 data/
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", port),
        make_handler(router, tmp, dialog_llm=dialog_llm, memory_root=tmp))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _post(port, path, body):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", path, body.encode(),
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


class ProfileConversationTest(unittest.TestCase):
    """profile / conversation 两只读端点 × dialog 写回联动。"""

    @classmethod
    def setUpClass(cls):
        cls.router = FakeRouter()
        cls.llm = ScriptLLM()
        cls.httpd, cls.port = _start_server(cls.router, dialog_llm=cls.llm)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        # identify 写图谱默认落 data/graph_profile_test.json → 清理测试产物
        p = os.path.join(_PROJECT_ROOT, "data", "graph_profile_test.json")
        if os.path.exists(p):
            os.remove(p)

    def setUp(self):
        self.llm.responses = list(_DIALOG_SCRIPT)

    def _dialog(self, text="我想买苹果很多。", conversation_id="s-a"):
        return _post(self.port, "/api/dialog",
                     json.dumps({"text": text, "conversation_id": conversation_id}))

    # ---------------- GET /api/profile ----------------

    def test_profile_contract_shape(self):
        st, out = _get(self.port, "/api/profile")
        self.assertEqual(st, 200)
        for k in ("learner_id", "profile", "common_errors", "summary_text",
                  "sessions", "stats"):
            self.assertIn(k, out)
        self.assertEqual(out["learner_id"], "profile_user")
        for k in ("graph_nodes", "graph_edges", "review_count",
                  "repeat_offenders"):
            self.assertIn(k, out["stats"])

    def test_stats_after_dialog_identify_hit(self):
        st, _ = self._dialog()
        self.assertEqual(st, 200)
        st, out = _get(self.port, "/api/profile")
        self.assertEqual(st, 200)
        # 直写节点：图谱 1 节点、进复习队列（mastery=0）
        self.assertEqual(out["stats"]["graph_nodes"], 1)
        self.assertGreaterEqual(out["stats"]["review_count"], 1)
        self.assertIsInstance(out["stats"]["repeat_offenders"], list)
        facts = out["common_errors"]
        self.assertTrue(any(f.get("kp_id") == "kp-order-many" for f in facts))
        self.assertEqual(out["stats"]["review_count"], len(facts))

    def test_sessions_auto_title_and_count(self):
        st, _ = self._dialog(text="把字句是什么意思？", conversation_id="s-title")
        self.assertEqual(st, 200)
        st, out = _get(self.port, "/api/profile")
        self.assertEqual(st, 200)
        sess = [s for s in out["sessions"] if s["id"] == "s-title"]
        self.assertEqual(len(sess), 1)
        # 首条消息自动设为标题（前 18 字）；user+assistant 各一条
        self.assertEqual(sess[0]["title"], "把字句是什么意思？")
        self.assertEqual(sess[0]["message_count"], 2)
        self.assertEqual(sess[0]["status"], "active")

    def test_sessions_sorted_by_updated_at_desc(self):
        for cid in ("s-ord-1", "s-ord-2"):
            st, _ = self._dialog(conversation_id=cid)
            self.assertEqual(st, 200)
        st, out = _get(self.port, "/api/profile")
        self.assertEqual(st, 200)
        ups = [s["updated_at"] for s in out["sessions"]]
        self.assertEqual(ups, sorted(ups, reverse=True))

    def test_long_first_message_title_truncated(self):
        long_text = "今天我想讨论一下汉语里把字句的具体用法和常见偏误还有和被字句的区别"
        st, _ = self._dialog(text=long_text, conversation_id="s-long")
        self.assertEqual(st, 200)
        st, out = _get(self.port, "/api/profile")
        sess = next(s for s in out["sessions"] if s["id"] == "s-long")
        self.assertEqual(sess["title"], long_text[:18])

    # ---------------- GET /api/conversation ----------------

    def test_conversation_history_restore(self):
        st, _ = self._dialog(conversation_id="s-hist")
        self.assertEqual(st, 200)
        st, out = _get(self.port,
                       "/api/conversation?id=" + "s-hist")
        self.assertEqual(st, 200)
        self.assertEqual(out["conversation_id"], "s-hist")
        msgs = out["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[0]["content"], "我想买苹果很多。")
        self.assertEqual(msgs[1]["content"], "已检查这句话。")
        # 0.27 契约：user 仍纯 {role,content}；assistant 会带白名单 cards/why，
        # 但绝不泄漏 skills/fallback 等其余 metadata 内部项
        for m in msgs:
            self.assertEqual(set(m.keys()) & {"skills", "fallback"}, set())
        self.assertEqual(set(msgs[0].keys()), {"role", "content"})
        self.assertTrue({"role", "content"} <= set(msgs[1].keys()))
        if "cards" in msgs[1]:
            self.assertIsInstance(msgs[1]["cards"], list)

    def test_conversation_multi_turn_order(self):
        for _ in range(2):
            st, _ = self._dialog(conversation_id="s-multi")
            self.assertEqual(st, 200)
        st, out = _get(self.port, "/api/conversation?id=s-multi")
        self.assertEqual(st, 200)
        roles = [m["role"] for m in out["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user", "assistant"])

    def test_conversation_unknown_id_empty(self):
        st, out = _get(self.port, "/api/conversation?id=no-such-session")
        self.assertEqual(st, 200)
        self.assertEqual(out["messages"], [])
        self.assertEqual(out["conversation_id"], "no-such-session")

    def test_conversation_default_id_param(self):
        # 缺 id 参数 → default 会话（与 dialog 缺省一致），不 500
        st, out = _get(self.port, "/api/conversation")
        self.assertEqual(st, 200)
        self.assertIn("messages", out)

    # ---------------- 交叉一致性 ----------------

    def test_profile_sessions_match_conversation_payload(self):
        st, _ = self._dialog(text="你好呀", conversation_id="s-xcheck")
        self.assertEqual(st, 200)
        _, prof = _get(self.port, "/api/profile")
        sess = next(s for s in prof["sessions"] if s["id"] == "s-xcheck")
        _, conv = _get(self.port, "/api/conversation?id=s-xcheck")
        # 会话元信息计数 = 实际消息条数（两处数据源一致）
        self.assertEqual(sess["message_count"], len(conv["messages"]))


class RepeatOffenderTest(unittest.TestCase):
    """独立服务器（独立 ledger）：撞错达 REPEAT_THRESHOLD=3 次 → 惯犯标记。"""

    @classmethod
    def setUpClass(cls):
        cls.router = FakeRouter()
        cls.llm = ScriptLLM()
        cls.httpd, cls.port = _start_server(cls.router, dialog_llm=cls.llm)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        p = os.path.join(_PROJECT_ROOT, "data", "graph_profile_test.json")
        if os.path.exists(p):
            os.remove(p)

    def setUp(self):
        self.llm.responses = list(_DIALOG_SCRIPT)

    def test_repeat_offender_after_threshold(self):
        for _ in range(3):
            self.llm.responses = list(_DIALOG_SCRIPT)
            st, _ = _post(self.port, "/api/dialog",
                          json.dumps({"text": "我想买苹果很多。",
                                      "conversation_id": "s-repeat"}))
            self.assertEqual(st, 200)
        st, out = _get(self.port, "/api/profile")
        self.assertEqual(st, 200)
        # 撞错 3 次达到阈值 → 惯犯计数 + facts 标记（报告卡"建议优先巩固"数据源）
        self.assertEqual(out["stats"]["repeat_offenders"], ["kp-order-many"])
        fact = next(f for f in out["common_errors"]
                    if f.get("kp_id") == "kp-order-many")
        self.assertTrue(fact.get("repeat_offender"))
        self.assertEqual(fact.get("repeated"), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)

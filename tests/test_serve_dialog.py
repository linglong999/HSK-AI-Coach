# ============================================================
# /api/dialog 端到端测试（0.17 · planner 唯一对话入口 + 直切改造）
# 覆盖：
#   - A3 fail-loud：无 Key → 400 code=llm_not_configured（不静默降级）
#   - 注入 mock LLM + 假引擎：200 契约 v1（text/used_skills/trace/steps/fallback/degraded/graph）
#   - A1 trace：planner 自主调 identify_errors → trace 记 name/params/ok/result
#   - dialog 模式图谱生长：identify 写图谱 → graph_snapshot 有节点（地图不死）
#   - 空 text → 400；坏 JSON → 400；未知路由 → 404
# 运行: python -m unittest tests.test_serve_dialog -v
# ============================================================

import http.client
import json
import os
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
from planner.loop import Planner
from skills import build_registry
from skills.identify_errors import IdentifyErrorsSkill


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
    """最小 Router 替身：learner_id + 共享图谱 + 假识别引擎（planner 装配取这些实例）。"""

    learner_id = "dialog_user"

    def __init__(self):
        self.graph = ErrorGraph("dialog_test")
        self.recognizer = FakeRecognizer()
        self.verifier = None
        self.explainer = None


class ScriptLLM:
    """脚本化 llm_call：按序返回响应；耗尽后返回固定 text 收尾。"""

    def __init__(self, responses=None):
        self.responses = list(responses or [])

    def __call__(self, messages):
        if self.responses:
            return self.responses.pop(0)
        return '[{"type":"text","content":"好的。"}]'


def _start_server(router, dialog_llm=None):
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html>dialog</html>")
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    # memory_root 指向临时目录：dialog 测试的记忆落盘不污染 data/
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", port),
        make_handler(router, tmp, dialog_llm=dialog_llm, memory_root=tmp))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _post(port, path, body_bytes):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", path, body_bytes,
              {"Content-Type": "application/json"})
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw.decode())
    except Exception:
        return r.status, None


# ---------------- A3：无 Key fail-loud ----------------

class DialogFailLoudTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.router = FakeRouter()
        cls.httpd, cls.port = _start_server(cls.router)  # 不注入 mock → 走真实 Key 检查

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_no_key_400_fail_loud(self):
        with mock.patch("config.settings.DEEPSEEK_API_KEY", ""), \
             mock.patch("config.settings.LLM_PROVIDER", "deepseek"):
            st, out = _post(self.port, "/api/dialog",
                            json.dumps({"text": "我想买苹果很多。"}).encode())
        self.assertEqual(st, 400)
        self.assertEqual(out.get("code"), "llm_not_configured")
        self.assertIn("API Key", out.get("error", ""))

    def test_empty_text_400(self):
        st, out = _post(self.port, "/api/dialog", json.dumps({"text": "  "}).encode())
        self.assertEqual(st, 400)
        self.assertEqual(out.get("error"), "empty text")

    def test_bad_json_body_400(self):
        st, out = _post(self.port, "/api/dialog", b"{bad")
        self.assertEqual(st, 400)
        self.assertEqual(out.get("error"), "invalid json body")

    def test_unknown_route_404(self):
        st, _ = _post(self.port, "/api/nothere", b"{}")
        self.assertEqual(st, 404)


# ---------------- 契约 v1 + trace + 图谱生长 ----------------

class DialogTraceTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.router = FakeRouter()
        cls.llm = ScriptLLM([
            '[{"type":"action","name":"identify_errors",'
            '  "params":{"text":"我想买苹果很多。"}}]',
            '[{"type":"text","content":"已检查这句话。"}]',
        ])
        cls.httpd, cls.port = _start_server(cls.router, dialog_llm=cls.llm)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        # identify 写图谱默认落 data/graph_dialog_test.json → 清理测试产物
        p = os.path.join(_PROJECT_ROOT, "data", "graph_dialog_test.json")
        if os.path.exists(p):
            os.remove(p)

    def setUp(self):
        # 每个用例重新装填脚本（共享 llm 实例的响应会被上一用例耗尽）
        self.llm.responses = [
            '[{"type":"action","name":"identify_errors",'
            '  "params":{"text":"我想买苹果很多。"}}]',
            '[{"type":"text","content":"已检查这句话。"}]',
        ]

    def test_contract_v1_shape(self):
        st, out = _post(self.port, "/api/dialog",
                        json.dumps({"text": "我想买苹果很多。"}).encode())
        self.assertEqual(st, 200)
        self.assertEqual(out.get("dialog_version"), "v1")
        for k in ("text", "used_skills", "steps", "fallback", "trace",
                  "degraded", "graph", "learner_id"):
            self.assertIn(k, out)
        self.assertEqual(out.get("text"), "已检查这句话。")
        self.assertFalse(out.get("fallback"))
        self.assertIn("identify_errors", out.get("used_skills", []))

    def test_trace_records_skill_for_cards(self):
        st, out = _post(self.port, "/api/dialog",
                        json.dumps({"text": "我想买苹果很多。"}).encode())
        self.assertEqual(st, 200)
        trace = out.get("trace", [])
        self.assertTrue(any(t.get("name") == "identify_errors" for t in trace))
        t = next(t for t in trace if t.get("name") == "identify_errors")
        self.assertTrue(t.get("ok"))
        self.assertIn("errors", t.get("result", {}))
        self.assertEqual(t.get("params", {}).get("text"), "我想买苹果很多。")

    def test_dialog_grows_graph(self):
        # A1 之外的架构缺口验收：dialog 模式识别写图谱 → 地图继续生长（非死图）
        st, out = _post(self.port, "/api/dialog",
                        json.dumps({"text": "我想买苹果很多。"}).encode())
        self.assertEqual(st, 200)
        nodes = out.get("graph", {}).get("nodes", {})
        self.assertIn("kp-order-many", nodes)


# ---------------- planner trace 单元级（无 HTTP） ----------------

class PlannerTraceTest(unittest.TestCase):

    def test_trace_on_success_and_fallback(self):
        reg = build_registry(graph=ErrorGraph("plan_trace"))
        # 成功路径：action + text
        p = Planner(reg, llm_call=ScriptLLM([
            '[{"type":"action","name":"identify_errors","params":{"text":"x"}}]',
            '[{"type":"text","content":"done"}]',
        ]))
        r = p.run("x")
        self.assertFalse(r["fallback"])
        self.assertEqual(len(r["trace"]), 1)
        self.assertEqual(r["trace"][0]["name"], "identify_errors")
        self.assertTrue(r["trace"][0]["ok"])
        # fallback 路径：解析失败也带已积累 trace。
        # "不是JSON" 过短（≤10 字符）不入裸文本兜底；重试一次仍失败 → parse fallback，
        # 重试多耗一步：steps=2（step0 失败喂回 + step1 再失败）
        p2 = Planner(reg, llm_call=lambda m: "不是JSON")
        r2 = p2.run("y")
        self.assertTrue(r2["fallback"])
        self.assertEqual(r2.get("trace"), [])
        self.assertEqual(r2.get("steps"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
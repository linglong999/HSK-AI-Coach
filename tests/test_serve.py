# ============================================================
# 前端壳 HTTP 服务层测试（M10）
# 用 mock Router 固化契约往返，免 LLM 调用（纯本地 / 免 API Key）。
# 启动真实 ThreadingHTTPServer + http.client 端到端测：
#  - POST /api/process 契约 JSON 往返 + graph 快照注入
#  - GET  /api/graph   图谱快照
#  - GET  /            静态托管
#  - 路径穿越防护 / 空 text 400 / 坏 JSON 400
# 运行: python -m unittest tests.test_serve -v
# ============================================================

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.serve import make_handler
from http.server import ThreadingHTTPServer
import socket


class FakeRouter:
    learner_id = "tester"

    def process(self, text):
        return {
            "contract_version": "v1", "learner_id": self.learner_id,
            "user_level": "HSK3", "native_lang": "英语", "input_text": text,
            "errors": [{
                "error": {"fragment": "苹果很多", "correction": "很多苹果", "type": "语法",
                          "type_confident": True, "confidence": 0.9,
                          "knowledge_point_id": "kp-x", "uncertain": False},
                "explanation": {"explanation": "应把数量放名词前。",
                                "key_points": [{"id": "kp-1", "text": "量词语序"}],
                                "keywords": [], "uncertain_note": "",
                                "free_generated": False},
                "graph_write": {"status": "node_upsert", "kp_id": "kp-x"},
                "verification": None}],
            "uncertain": [], "has_error": True, "review_queue": [],
            "degraded": [], "meta": {"start_ts": ""},
        }

    @property
    def graph(self):
        return FakeGraph()

    def verify_rephrase(self, explanation, key_points, restatement,
                        uncertain=False, bias_ref=None, event_key=""):
        return {
            "verdict": "pass",
            "covered_points": 2, "total_points": 2, "coverage_ratio": 1.0,
            "point_judgements": [
                {"id": 1, "text": "量词语序", "is_covered": True, "evidence": "数量词放名词前"},
                {"id": 2, "text": "修饰语位置", "is_covered": True, "evidence": "修饰语前置"}],
            "flowery_but_empty": False, "feedback": "复述到位。",
            "consecutive_fail": 0, "action": None,
            "write_status": {"status": "node_update"}, "degraded": False,
            "_echo": {"restatement": restatement,
                      "n_points": len(key_points), "event_key": event_key},
        }


class FakeGraph:
    def graph_snapshot(self):
        return {
            "learner_id": "tester",
            "nodes": {"kp-x": {"id": "kp-x", "knowledge_point": "锚", "level": "HSK3",
                               "error_types": {"语法": 1}, "error_count": 1, "mastery": 0.0,
                               "last_learnt_at": None, "created_at": ""}},
            "edges": [], "queue": []}


class ServeTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        with open(os.path.join(cls.tmp, "index.html"), "w", encoding="utf-8") as f:
            f.write("<html>fake-index</html>")
        # 用固定端口避免竞争
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()
        router = FakeRouter()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port),
                                        make_handler(router, cls.tmp))
        cls.thr = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thr.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def test_process_roundtrip_contract(self):
        c = self._conn()
        c.request("POST", "/api/process",
                  json.dumps({"text": "我想买苹果很多。"}).encode(),
                  {"Content-Type": "application/json"})
        r = c.getresponse()
        body = json.loads(r.read().decode())
        c.close()
        self.assertEqual(r.status, 200)
        self.assertEqual(body["contract_version"], "v1")
        self.assertEqual(body["has_error"], True)
        self.assertEqual(body["input_text"], "我想买苹果很多。")
        self.assertIn("graph", body)
        self.assertIn("kp-x", body["graph"]["nodes"])

    def test_process_empty_400(self):
        c = self._conn()
        c.request("POST", "/api/process", json.dumps({"text": "  "}).encode())
        r = c.getresponse(); body = json.loads(r.read().decode()); c.close()
        self.assertEqual(r.status, 400)  # serve 空文本 → 400 error
        self.assertIn("error", body)

    def test_process_bad_json(self):
        c = self._conn()
        c.request("POST", "/api/process", b"{invalid", {"Content-Type": "application/json"})
        r = c.getresponse(); body = json.loads(r.read().decode()); c.close()
        self.assertIn("error", body)

    def test_verify_roundtrip_contract(self):
        c = self._conn()
        c.request("POST", "/api/verify",
                  json.dumps({"explanation": "应把数量放名词前。",
                              "key_points": [{"id": "kp-1", "text": "量词语序"}],
                              "restatement": "数量词要放在名词前面。"}).encode(),
                  {"Content-Type": "application/json"})
        r = c.getresponse()
        body = json.loads(r.read().decode())
        c.close()
        self.assertEqual(r.status, 200)
        self.assertEqual(body["verdict"], "pass")
        self.assertEqual(body["coverage_ratio"], 1.0)
        self.assertEqual(len(body["point_judgements"]), 2)
        # serve 层透传参数完整 + 稳定 event_key（原则4：实体本身做 key）
        self.assertEqual(body["_echo"]["restatement"], "数量词要放在名词前面。")
        self.assertEqual(body["_echo"]["n_points"], 1)
        self.assertEqual(body["_echo"]["event_key"], "web#数量词要放在名词前面。")

    def test_verify_empty_400(self):
        c = self._conn()
        c.request("POST", "/api/verify",
                  json.dumps({"restatement": "   "}).encode(),
                  {"Content-Type": "application/json"})
        r = c.getresponse()
        body = json.loads(r.read().decode())
        c.close()
        self.assertEqual(r.status, 400)
        self.assertIn("error", body)

    def test_graph_endpoint(self):
        c = self._conn()
        c.request("GET", "/api/graph")
        r = c.getresponse(); body = json.loads(r.read().decode()); c.close()
        self.assertIn("nodes", body)
        self.assertIn("kp-x", body["nodes"])
        self.assertIn("edges", body)
        self.assertIn("queue", body)

    def test_static_index(self):
        c = self._conn()
        c.request("GET", "/")
        r = c.getresponse(); body = r.read().decode(); c.close()
        self.assertEqual(r.status, 200)
        self.assertIn("<html>fake-index</html>", body)

    def test_path_traversal_blocked(self):
        c = self._conn()
        c.request("GET", "/../../../../etc/passwd")
        r = c.getresponse(); r.read(); c.close()
        # 穿越被 realpath 拦截 → 404
        self.assertEqual(r.status, 404)

    def test_unrouted_returns_404(self):
        c = self._conn()
        c.request("GET", "/nope")
        r = c.getresponse(); r.read(); c.close()
        self.assertEqual(r.status, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
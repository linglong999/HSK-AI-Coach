# ============================================================
# /api/generate 端到端测试（生成引擎接入服务层，0.16）
# 注入 mock LLM client → 免 Key 环境跑通契约往返：
#   - explain 合法单元 → ok:true + graph 快照
#   - LLM 失败（无 Key）→ 结构化 degraded（200 非 500）
#   - unit_type 非法 → 400
#   - practice 命中 kp → 两段式写回（writeback 记录）
# 运行: python -m unittest tests.test_serve_generate -v
# ============================================================

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
import socket

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.generation.generator import GenerationEngine
from engine.memory.writeback import Writeback
from engine.graph.error_graph import ErrorGraph
from engine.llm.client import JSONStrictError
from engine.serve import make_handler


def valid_explain():
    return {
        "id": "u-3", "type": "explain", "title": "量词只",
        "keyPoints": ["量词'只'用于个体量词"], "forbidden_errors": [],
        "context": {"curriculumAt": "kp:只"}, "self_language": "zh",
        "dialogueSpec": None, "interactiveSpec": None, "pblSpec": None,
        "for_keypoint": "量词只", "teachingObjective": "讲清量词只", "estimatedDuration": 90,
    }


def valid_practice():
    return {
        "id": "u-4", "type": "practice", "title": "量词只巩固",
        "keyPoints": ["量词'只'用于个体量词"], "forbidden_errors": [],
        "context": {"curriculumAt": "kp:只"}, "self_language": "zh",
        "dialogueSpec": None, "interactiveSpec": None, "pblSpec": None,
        "for_keypoints": ["量词只"],
        "targets_errors": [{"fragment": "*一只鸡", "knowledge_point_id": "kp:x"}],
        "task_kind": "mcq", "questionCount": 3, "difficulty": None,
    }


class MockClient:
    """脚本化 mock：chat_json_strict 按序弹出（dict | Exception）。耗尽抛 JSONStrictError。"""

    def __init__(self, responses):
        self.responses = list(responses)

    def chat_json_strict(self, system, user, temperature=0.2, retries=1):
        if not self.responses:
            raise JSONStrictError("mock exhausted")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class GenRouter:
    learner_id = "tester"

    def __init__(self):
        self.graph = ErrorGraph("gen_test")


class GenerateApiTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        with open(os.path.join(cls.tmp, "index.html"), "w", encoding="utf-8") as f:
            f.write("<html>gen</html>")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()
        cls.router = GenRouter()
        cls.graph = cls.router.graph
        cls.wb = Writeback(graph=cls.graph, learner_id="tester", root=cls.tmp)
        cls.generation = GenerationEngine(graph=cls.graph, writeback=cls.wb,
                                          client=MockClient([]))
        cls.httpd = ThreadingHTTPServer(
            ("127.0.0.1", cls.port),
            make_handler(cls.router, cls.tmp, generation=cls.generation))
        cls.thr = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thr.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _set_responses(self, responses):
        # 替换 mock 的响应序列（同实例，跨测试隔离）
        self.generation.client.responses = list(responses)

    def _post(self, body):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/api/generate", json.dumps(body).encode(),
                  {"Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read().decode())
        c.close()
        return r.status, data

    def test_generate_explain_ok(self):
        self._set_responses([valid_explain()])
        st, out = self._post({"unit_type": "explain", "for_keypoint": "量词只",
                              "fragment": "*一只鸡", "knowledge_point_id": "kp:只"})
        self.assertEqual(st, 200)
        self.assertTrue(out["ok"])
        self.assertEqual(out["unit"]["type"], "explain")
        self.assertIn("graph", out)            # 服务层注入快照
        self.assertIn("nodes", out["graph"])

    def test_generate_llm_fail_degrades_not_500(self):
        # 免 Key：LLM 调用失败 → 结构化 degraded（HTTP 200，非 500）
        self._set_responses([RuntimeError("no api key")])
        st, out = self._post({"unit_type": "explain", "for_keypoint": "量词只"})
        self.assertEqual(st, 200)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "degraded")
        self.assertIn("diagnostics", out)
        codes = [d["code"] for d in out["diagnostics"]]
        self.assertIn("code_llm_failed", codes)

    def test_generate_unknown_type_400(self):
        self._set_responses([])
        st, out = self._post({"unit_type": "dialogue"})
        self.assertEqual(st, 400)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "degraded")

    def test_generate_bad_json_body_400(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/api/generate", b"{bad",
                  {"Content-Type": "application/json"})
        r = c.getresponse(); body = json.loads(r.read().decode()); c.close()
        self.assertEqual(r.status, 400)
        self.assertIn("error", body)

    def test_generate_practice_writeback(self):
        # practice 命中已确认 kp → 两段式写回（writeback 记录）
        self._set_responses([valid_practice()])
        st, out = self._post({"unit_type": "practice",
                              "fragment": "*一只鸡", "knowledge_point_id": "kp:x"})
        self.assertEqual(st, 200)
        self.assertTrue(out["ok"])
        self.assertEqual(out["unit"]["type"], "practice")
        self.assertEqual(out["writeback"], ["kp:x"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
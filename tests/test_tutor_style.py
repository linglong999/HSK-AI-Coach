# ============================================================
# tests/test_tutor_style.py
# P0.16 · tutor 措辞规范（去 AI 味） + 情绪感知鼓励档
# 覆盖：
#   - persona.build_tutor_style_directive：zh/en 都非空（D1=A 无条件注入的措辞源）
#   - serve 主链：未配 persona 的新 learner 的 system 也含 [Style] 措辞（D1=A 端到端）
#   - 配了 persona 的 learner 同时含 [Persona] 与 [Style]（人设/措辞两层正交）
#   - intervention.detect_frustration：挫败词表命中
#   - decide_intervention：求助优先于挫败（同句求助 → block/help，不误判为鼓励）
#   - decide_intervention：挫败 → encourage/frustrated；cap 顶 → 仍 none/cap
#   - build_intervention_directive('encourage')：含"共情→降要求→小成功"三段，双语
# 运行: python -m unittest tests.test_tutor_style -v
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
from engine.intervention import (
    detect_frustration, decide_intervention, build_intervention_directive)
from engine.persona import build_tutor_style_directive
from engine.serve import make_handler


# ---------------- persona 措辞源（D1=A） ----------------

class TutorStyleDirectiveTest(unittest.TestCase):

    def test_zh_and_en_nonempty(self):
        self.assertTrue(build_tutor_style_directive("zh").strip())
        self.assertTrue(build_tutor_style_directive("en").strip())
        self.assertTrue(build_tutor_style_directive("").strip())  # 默认也注入

    def test_zh_has_core_spec(self):
        d = build_tutor_style_directive("zh")
        self.assertIn("[Style]", d)
        self.assertIn("短句", d)
        self.assertIn("先肯定", d)
        self.assertIn("一次只问一个问题", d)

    def test_en_has_core_spec(self):
        d = build_tutor_style_directive("en")
        self.assertIn("[Style]", d)
        self.assertIn("ONE question at a time", d)
        self.assertIn("Accuracy", d)


# ---------------- 情绪感知 · 挫败判定 ----------------

class FrustrationDetectTest(unittest.TestCase):

    def test_detects_frustration(self):
        self.assertTrue(detect_frustration("中文太难了，我学不会"))
        self.assertTrue(detect_frustration("好烦，我就是记不住"))
        self.assertTrue(detect_frustration("太累了，没信心继续了"))

    def test_not_false_positive_on_plain(self):
        # 场景产出句子里的"难/累"不误判（宁漏勿错）
        self.assertFalse(detect_frustration("这道题不算难，我能做"))
        self.assertFalse(detect_frustration("昨天我走了很多路，有点累"))

    def test_empty_returns_false(self):
        self.assertFalse(detect_frustration(""))


class DecideEncourageTest(unittest.TestCase):

    def test_frustration_yields_encourage(self):
        self.assertEqual(decide_intervention("太难了，我学不会")[0], "encourage")
        self.assertEqual(decide_intervention("太难了，我学不会")[1], "frustrated")

    def test_help_priority_over_frustration(self):
        # 同句求助优先：命中求助正则 → block/help，不落到鼓励
        for sent in ("太难了，这个怎么说？", "这个什么意思，好难", "教我怎么学，记不住"):
            level, reason = decide_intervention(sent)
            self.assertEqual((level, reason), ("block", "help"),
                             f"求助被误判为鼓励: {sent}")

    def test_cap_blocks_encourage(self):
        self.assertEqual(decide_intervention("太难了", interrupt_used=3, cap=3),
                         ("none", "cap"))

    def test_normal_fluent_unchanged(self):
        self.assertEqual(decide_intervention("我昨天去了公园"),
                         ("none", "fluent"))

    def test_encourage_not_consuming_interrupt_usage(self):
        from engine.intervention import InterventionTracker
        t = InterventionTracker()
        level, reason = t.observe("太难了我记不住")
        self.assertEqual(level, "encourage")
        self.assertEqual(t.interrupt_used, 0)  # 鼓励不占打断预算


class EncourageDirectiveTest(unittest.TestCase):

    def _bilingual(self, lang):
        d = build_intervention_directive("encourage", "frustrated", lang)
        self.assertTrue(d)
        self.assertIn("[Intervention]", d)
        return d

    def test_zh_three_step(self):
        d = self._bilingual("zh")
        self.assertIn("共情", d)
        self.assertIn("降当轮要求", d)
        self.assertIn("小任务", d)
        self.assertIn("鼓励模式", d)

    def test_en_three_step(self):
        d = self._bilingual("en")
        self.assertIn("empathetic", d)
        self.assertIn("lower the bar", d)
        self.assertIn("small task", d)
        self.assertIn("encouragement", d)


# ---------------- serve 主链注入（D1=A 端到端） ----------------

class FakeRecognizer:
    def recognize(self, text, level=3, native_lang=""):
        return {"errors": [], "uncertain": [], "degraded": []}


class CaptureLLM:
    def __init__(self):
        self.calls = []
    def __call__(self, messages):
        self.calls.append(copy.deepcopy(messages))
        return '[{"type":"text","content":"好的。"}]'


def make_fake_router(learner_id):
    class FakeRouter:
        pass
    r = FakeRouter()
    r.learner_id = learner_id
    r.graph = ErrorGraph(f"s16_{learner_id}")
    r.recognizer = FakeRecognizer()
    r.verifier = None
    r.explainer = None
    return r


def _start_server(router, dialog_llm):
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html>s16</html>")
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


class ServeTutorStyleTest(unittest.TestCase):

    LEARNER = "s16_tut"

    @classmethod
    def setUpClass(cls):
        cls.router = make_fake_router(cls.LEARNER)
        cls.llm = CaptureLLM()
        cls.httpd, cls.port, cls.root = _start_server(cls.router, cls.llm)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        p = os.path.join(_PROJECT_ROOT, "data", f"graph_s16_{cls.LEARNER}.json")
        if os.path.exists(p):
            os.remove(p)

    def _system_for(self, learner=None, conv="conv-s16"):
        self.llm.calls.clear()
        body = {"text": "你好", "learner_id": learner or self.LEARNER,
                "conversation_id": conv, "native_lang": "zh"}
        st, _ = _post(self.port, "/api/dialog", body)
        self.assertEqual(st, 200)
        return self.llm.calls[0][0]["content"]

    def test_fresh_learner_injects_style(self):
        # D1=A：未配 persona 的新 learner，system 也含 [Style] 措辞
        sys_ = self._system_for(learner=self.LEARNER + "_fresh", conv="c-fresh")
        self.assertIn("[Style]", sys_)
        self.assertIn("短句", sys_)

    def test_persona_keeps_both_layers(self):
        st, _ = _post(self.port, "/api/profile", {
            "learner": self.LEARNER, "persona": {"reply_style": "friendly"}})
        self.assertEqual(st, 200)
        sys_ = self._system_for()
        self.assertIn("[Persona]", sys_)   # 人设层（配了 persona）
        self.assertIn("[Style]", sys_)     # 措辞层（无条件）


if __name__ == "__main__":
    unittest.main(verbosity=2)
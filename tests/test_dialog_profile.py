# ============================================================
# /api/dialog × M8 画像/惯犯 集成测试（0.18 接入项③）
# 覆盖（agent-design M8 验收标准）：
#   - 两段式账本：识别命中 → ledger observation_error（惯犯数据源）
#   - 确认侧：verify_retell pass → concept_confirmed（fail 不确认）
#   - 画像注入：3 次同 KP 偏误后 system 含【学习者画像】+ 惯犯标记
#   - 三线汇合：常错点 facts 落 M5 profile.common_errors
#   - 冷启动：空图谱首轮无画像段、profile 无 common_errors
# 运行: python -m unittest tests.test_dialog_profile -v
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
from engine.memory.error_ledger import ErrorLedger
from engine.memory.learner_memory import LearnerMemory
from engine.serve import make_handler


# ---------------- 测试替身 ----------------

class FakeRecognizer:
    """含「苹果很多」片段的句子返回固定已确认偏误；其余句子干净零命中。
    （0.22 方向3：serve 对每句预扫识别，替身必须对齐真实识别器
    "干净句零误报"的宁漏勿错契约——否则预扫会把干净句打成偏误。）"""

    def recognize(self, text, level=3, native_lang=""):
        if "苹果很多" not in str(text or ""):
            return {"errors": [], "uncertain": [], "degraded": []}
        return {
            "errors": [{
                "fragment": "苹果很多", "correction": "很多苹果",
                "type": "语法-语序", "type_confident": True, "confidence": 0.9,
                "knowledge_point_id": "kp-order-many", "uncertain": False,
            }],
            "uncertain": [], "degraded": [],
        }


class FakeVerifier:
    """固定裁决的验证替身（pass/fail 可配）。"""

    def __init__(self, verdict="pass"):
        self.verdict = verdict

    def verify(self, explanation="", key_points=None, restatement="",
               uncertain=False, bias_ref=None, event_key=None, commit_graph=True,
               native_lang=""):
        return {
            "point_judgements": [{"id": "k1", "judgement": "covered"}],
            "verdict": self.verdict,
            "covered_points": 1, "total_points": 1,
            "coverage_ratio": 1.0, "degraded": False, "next": "",
        }


def make_fake_router(learner_id, verifier=None):
    class FakeRouter:
        pass
    r = FakeRouter()
    r.learner_id = learner_id
    r.graph = ErrorGraph(f"dialog_{learner_id}")
    r.recognizer = FakeRecognizer()
    r.verifier = verifier
    r.explainer = None
    return r


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


def _start_server(router, dialog_llm):
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html>m8</html>")
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


def _cleanup_graph(learner_id):
    p = os.path.join(_PROJECT_ROOT, "data", f"graph_dialog_{learner_id}.json")
    if os.path.exists(p):
        os.remove(p)


IDENTIFY_ACTION = ('[{"type":"action","name":"identify_errors",'
                   '  "params":{"text":"我想买苹果很多。"}}]')
TEXT_REPLY = '[{"type":"text","content":"%s"}]'


def _identify_round(llm, text="已检查。"):
    llm.reset([IDENTIFY_ACTION, TEXT_REPLY % text])


# ---------------- 惯犯闭环 + 画像注入 + facts 汇合 ----------------

class ProfileRepeatOffenderTest(unittest.TestCase):
    """同一 KP 连错 3 次 → 惯犯标记进 system，facts 落 M5 profile。"""

    LEARNER = "m8_flow"

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

    def test_full_flow(self):
        # 轮 1-3：每轮识别一次同 KP 偏误
        for i in range(3):
            _identify_round(self.llm, text=f"第{i+1}次讲解。")
            st, out = _post(self.port, "/api/dialog", {
                "text": "我想买苹果很多。", "learner_id": self.LEARNER,
                "conversation_id": "conv-m8"})
            self.assertEqual(st, 200)
            self.assertIn("identify_errors", out.get("used_skills", []))

        # 账本侧：3 条 observation_error，构成惯犯（阈值>=3）
        ledger = ErrorLedger(self.LEARNER, root=self.root)
        self.assertEqual(ledger.repeated_count("kp-order-many"), 3)
        self.assertTrue(ledger.is_repeat_offender("kp-order-many"))
        kinds = [e["kind"] for e in ledger.recent()]
        self.assertEqual(kinds.count("observation_error") + kinds.count("repeated_error"), 3)

        # 轮 4（无识别）：system 注入【学习者画像】且带惯犯标记
        self.llm.reset([TEXT_REPLY % "这次不查句子。"])
        st, out = _post(self.port, "/api/dialog", {
            "text": "今天先这样", "learner_id": self.LEARNER,
            "conversation_id": "conv-m8"})
        self.assertEqual(st, 200)
        system = self.llm.calls[0][0]
        self.assertEqual(system["role"], "system")
        self.assertIn("## 常错点", system["content"])      # 画像正文（非规则文本）
        self.assertIn("kp-order-many", system["content"])
        self.assertIn("惯犯", system["content"])

        # 三线汇合：facts 落 M5 profile.common_errors
        prof = LearnerMemory(self.LEARNER, root=self.root).get_profile()
        facts = prof.get("common_errors", [])
        self.assertTrue(facts)
        f0 = next(f for f in facts if f["kp_id"] == "kp-order-many")
        self.assertEqual(f0["repeated"], 3)
        self.assertTrue(f0["repeat_offender"])
        self.assertEqual(f0["latest_evidence"], "我想买苹果很多。")
        self.assertIn("updated_at", f0)


# ---------------- 确认侧：verify pass → concept_confirmed ----------------

class VerifyConfirmLedgerTest(unittest.TestCase):
    """识别 + 复述验证：pass 记 concept_confirmed；fail 不记。"""

    LEARNER = "m8_confirm"

    @classmethod
    def setUpClass(cls):
        cls.router = make_fake_router(cls.LEARNER, verifier=FakeVerifier("pass"))
        cls.llm = CaptureLLM()
        cls.httpd, cls.port, cls.root = _start_server(cls.router, cls.llm)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        _cleanup_graph(cls.LEARNER)

    def _run_verify_round(self, conv):
        verify_action = ('[{"type":"action","name":"verify_retell","params":{'
                         '"explanation":"语序讲解","key_points":[{"id":"k1","text":"很多在名词前"}],'
                         '"restatement":"很多要放在名词前面"}}]')
        self.llm.reset([IDENTIFY_ACTION, verify_action, TEXT_REPLY % "验证完成。"])
        return _post(self.port, "/api/dialog", {
            "text": "我想买苹果很多。", "learner_id": self.LEARNER,
            "conversation_id": conv})

    def test_verify_verdict_controls_confirmation(self):
        # 轮 1：verify pass → concept_confirmed 落账（确认侧闭环）
        st, out = self._run_verify_round("conv-pass")
        self.assertEqual(st, 200)
        self.assertIn("verify_retell", out.get("used_skills", []))
        ledger = ErrorLedger(self.LEARNER, root=self.root)
        events = [e for e in ledger.recent() if e["kp_id"] == "kp-order-many"]
        self.assertEqual(len(events), 2)            # 1 观察 + 1 确认
        self.assertEqual(events[-1]["kind"], "concept_confirmed")

        # 轮 2：verify fail → 只记观察，不新增确认
        self.router.verifier.verdict = "fail"
        try:
            st, out = self._run_verify_round("conv-fail")
            self.assertEqual(st, 200)
            ledger.reload()
            events = [e for e in ledger.recent() if e["kp_id"] == "kp-order-many"]
            self.assertEqual(len(events), 3)        # 2 观察 + 1 确认（fail 未确认）
            confirmed = [e for e in events if e["kind"] == "concept_confirmed"]
            self.assertEqual(len(confirmed), 1)
        finally:
            self.router.verifier.verdict = "pass"


# ---------------- 跨轮确认：识别轮与复述轮分离（真实流程） ----------------

class CrossRoundConfirmTest(unittest.TestCase):
    """轮 1 识别讲解（无验证），轮 2 仅复述通过 → 账本兜底确认（0.18 项③跨轮缺口）。"""

    LEARNER = "m8_cross"

    @classmethod
    def setUpClass(cls):
        cls.router = make_fake_router(cls.LEARNER, verifier=FakeVerifier("pass"))
        cls.llm = CaptureLLM()
        cls.httpd, cls.port, cls.root = _start_server(cls.router, cls.llm)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        _cleanup_graph(cls.LEARNER)

    def test_cross_round_confirmation(self):
        # 轮 1：识别（讲解并邀请复述，无验证）
        _identify_round(self.llm, text="很多要放在名词前，你用自己的话复述一遍。")
        st, out = _post(self.port, "/api/dialog", {
            "text": "我想买苹果很多。", "learner_id": self.LEARNER,
            "conversation_id": "conv-cross"})
        self.assertEqual(st, 200)
        ledger = ErrorLedger(self.LEARNER, root=self.root)
        self.assertEqual(ledger.repeated_count("kp-order-many"), 1)
        self.assertFalse(any(e["kind"] == "concept_confirmed" for e in ledger.recent()))

        # 轮 2：仅复述验证（无识别）→ 跨轮兜底确认
        verify_action = ('[{"type":"action","name":"verify_retell","params":{'
                         '"explanation":"语序讲解","key_points":[{"id":"k1","text":"很多在名词前"}],'
                         '"restatement":"很多要放在名词前面"}}]')
        self.llm.reset([verify_action, TEXT_REPLY % "很好，理解到位。"])
        st, out = _post(self.port, "/api/dialog", {
            "text": "很多要放在名词前面", "learner_id": self.LEARNER,
            "conversation_id": "conv-cross"})
        self.assertEqual(st, 200)
        ledger.reload()
        events = [e for e in ledger.recent() if e["kp_id"] == "kp-order-many"]
        kinds = [e["kind"] for e in events]
        self.assertIn("concept_confirmed", kinds)
        self.assertEqual(len(events), 2)   # 1 观察 + 1 跨轮确认


# ---------------- 冷启动：无画像不注入 ----------------

class FreshDialogNoProfileTest(unittest.TestCase):
    """空图谱首轮：system 无画像段；profile 无 common_errors（不写空 facts）。"""

    LEARNER = "m8_fresh"

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

    def test_first_round_no_profile(self):
        self.llm.reset([TEXT_REPLY % "你好，把句子发给我看看。"])
        st, out = _post(self.port, "/api/dialog", {
            "text": "你好", "learner_id": self.LEARNER,
            "conversation_id": "conv-0"})
        self.assertEqual(st, 200)
        system = self.llm.calls[0][0]
        self.assertEqual(system["role"], "system")
        self.assertNotIn("## 常错点", system["content"])   # 无画像正文（规则文本不算）
        prof = LearnerMemory(self.LEARNER, root=self.root).get_profile()
        self.assertNotIn("common_errors", prof)


if __name__ == "__main__":
    unittest.main(verbosity=2)

# ============================================================
# tests/test_persona.py
# 0.22 方向3 · 个性化栏 纯函数 + serve 端点/注入集成测试
# 覆盖（设计稿 §3.2/§3.3/§4）：
#   - normalize_persona：六档枚举 / cap 纠正钳制 / 文本清洗截长 /
#     未知键丢弃 / 部分合并（current 沿用）/ fail-loud
#   - build_persona_brief：双语注入段 / identity 优先于 Scene /
#     全默认回退空串（0.21 语言指令默认）
#   - serve：POST /api/profile 保存 + GET 带回 + 非法 400 +
#     dialog 主链 [Persona] 注入 + persona.interrupt_cap 消费
# 运行: python -m unittest tests.test_persona -v
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
from engine.persona import (
    REPLY_STYLES, DEFAULT_PERSONA, normalize_persona, build_persona_brief)
from engine.serve import make_handler


# ---------------- normalize_persona ----------------

class NormalizePersonaTest(unittest.TestCase):

    def test_defaults(self):
        p = normalize_persona({})
        self.assertEqual(p, DEFAULT_PERSONA)

    def test_all_six_styles_legal(self):
        for s in REPLY_STYLES:
            self.assertEqual(normalize_persona({"reply_style": s})["reply_style"], s)

    def test_invalid_style_raises(self):
        with self.assertRaises(ValueError):
            normalize_persona({"reply_style": "sarcastic"})

    def test_style_case_insensitive(self):
        self.assertEqual(
            normalize_persona({"reply_style": "Socratic"})["reply_style"], "socratic")

    def test_cap_coerced_and_clamped(self):
        self.assertEqual(normalize_persona({"interrupt_cap": "5"})["interrupt_cap"], 5)
        self.assertEqual(normalize_persona({"interrupt_cap": 99})["interrupt_cap"], 10)
        self.assertEqual(normalize_persona({"interrupt_cap": -1})["interrupt_cap"], 0)
        self.assertEqual(normalize_persona({"interrupt_cap": "x"})["interrupt_cap"], 3)

    def test_text_truncated(self):
        p = normalize_persona({"address": "玲" * 60})
        self.assertEqual(len(p["address"]), 50)
        p = normalize_persona({"custom_instructions": "错" * 600})
        self.assertEqual(len(p["custom_instructions"]), 500)

    def test_unknown_keys_dropped(self):
        p = normalize_persona({"reply_style": "friendly", "evil_key": "rm -rf"})
        self.assertNotIn("evil_key", p)
        self.assertEqual(sorted(p), sorted(DEFAULT_PERSONA))

    def test_partial_merge_keeps_current(self):
        current = {"reply_style": "socratic", "address": "Ling",
                   "identity": "", "custom_instructions": "", "interrupt_cap": 5}
        p = normalize_persona({"address": "小玲"}, current=current)
        self.assertEqual(p["reply_style"], "socratic")   # 沿用现值
        self.assertEqual(p["address"], "小玲")            # 只更新给出字段
        self.assertEqual(p["interrupt_cap"], 5)

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError):
            normalize_persona("friendly")

    def test_control_chars_stripped(self):
        p = normalize_persona({"identity": "陪练\x00友\x1f"})
        self.assertEqual(p["identity"], "陪练友")


# ---------------- build_persona_brief ----------------

class BuildPersonaBriefTest(unittest.TestCase):

    def test_empty_returns_blank(self):
        self.assertEqual(build_persona_brief(None, "zh"), "")
        self.assertEqual(build_persona_brief({}, "zh"), "")
        # 全默认 → 回退 0.21 语言指令默认，不加 [Persona] 段
        self.assertEqual(build_persona_brief(DEFAULT_PERSONA, "zh"), "")

    def test_zh_brief_sections(self):
        brief = build_persona_brief(
            {"reply_style": "friendly", "address": "小玲",
             "identity": "中文陪练友", "custom_instructions": "一次只聚焦一个错",
             "interrupt_cap": 3}, "zh")
        self.assertIn("[Persona]", brief)
        self.assertIn("亲和友善", brief)
        self.assertIn("称呼我：小玲", brief)
        self.assertIn("你的身份：中文陪练友", brief)
        self.assertIn("自定义指令：一次只聚焦一个错", brief)
        self.assertIn("打断频率上限：3", brief)

    def test_en_brief_sections(self):
        brief = build_persona_brief(
            {"reply_style": "socratic", "address": "Ling",
             "identity": "my practice buddy", "interrupt_cap": 2}, "en")
        self.assertIn("[Persona]", brief)
        self.assertIn("socratic", brief)
        self.assertIn("Address me as: Ling", brief)
        self.assertIn("Your identity: my practice buddy", brief)
        self.assertIn("Interruption cap: 2", brief)

    def test_identity_precedence_note(self):
        # 共处规则：identity 优先于 [Scene] 角色（设计稿 §3.3）
        self.assertIn("优先于 [Scene]",
                      build_persona_brief({"identity": "陪练"}, "zh"))
        self.assertIn("takes precedence over the [Scene] role",
                      build_persona_brief({"identity": "buddy"}, "en"))

    def test_cap_only_persona_still_briefs(self):
        # 仅调 cap（无风格/文本）→ 仍产出 [Persona]（cap 告知 LLM）
        brief = build_persona_brief({"interrupt_cap": 1}, "zh")
        self.assertIn("[Persona]", brief)
        self.assertIn("打断频率上限：1", brief)

    def test_invalid_persona_returns_blank(self):
        # 非法 style：brief 静默回退空串（不注入坏约束）
        self.assertEqual(
            build_persona_brief({"reply_style": "sarcastic"}, "zh"), "")


# ---------------- serve 端点 / 主链注入 ----------------

class FakeRecognizer:
    def recognize(self, text, level=3, native_lang=""):
        return {"errors": [], "uncertain": [], "degraded": []}


class CaptureLLM:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def __call__(self, messages):
        self.calls.append(copy.deepcopy(messages))
        if self.responses:
            return self.responses.pop(0)
        return '[{"type":"text","content":"好的。"}]'

    def reset(self, responses):
        self.responses = list(responses)
        self.calls.clear()


TEXT_REPLY = '[{"type":"text","content":"%s"}]'


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
        f.write("<html>s3p</html>")
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


class ServePersonaTest(unittest.TestCase):

    LEARNER = "s3_pers"

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

    FULL = {"reply_style": "friendly", "address": "Ling",
            "identity": "my practice buddy",
            "custom_instructions": "one error at a time",
            "interrupt_cap": 5}

    def _save(self, persona, learner=None):
        body = {"persona": persona}
        if learner:
            body["learner"] = learner
        return _post(self.port, "/api/profile", body)

    def test_post_get_roundtrip(self):
        st, out = self._save(self.FULL)
        self.assertEqual(st, 200)
        self.assertTrue(out.get("ok"))
        # 返回规范化五键全量
        self.assertEqual(out["persona"]["reply_style"], "friendly")
        self.assertEqual(out["persona"]["interrupt_cap"], 5)
        # GET /api/profile 带回 profile.persona
        st, prof = _get(self.port, "/api/profile?learner=" + self.LEARNER)
        self.assertEqual(st, 200)
        self.assertEqual(prof["profile"]["persona"]["address"], "Ling")
        self.assertEqual(prof["profile"]["persona"]["identity"],
                         "my practice buddy")

    def test_post_invalid_style_400(self):
        st, out = self._save({"reply_style": "sarcastic"})
        self.assertEqual(st, 400)
        self.assertEqual(out.get("code"), "invalid_persona")
        self.assertIn("reply_style", out.get("error", ""))

    def test_post_non_object_400(self):
        st, out = _post(self.port, "/api/profile", {"persona": "friendly"})
        self.assertEqual(st, 400)
        self.assertEqual(out.get("code"), "invalid_persona")

    def test_partial_update_keeps_existing(self):
        # 仅改称呼 → 其余字段沿用现值（部分合并语义；测试自足：先存全量）
        st, _ = self._save(self.FULL)
        self.assertEqual(st, 200)
        st, out = self._save({"address": "小玲"})
        self.assertEqual(st, 200)
        self.assertEqual(out["persona"]["address"], "小玲")
        self.assertEqual(out["persona"]["reply_style"], "friendly")
        self.assertEqual(out["persona"]["interrupt_cap"], 5)

    def test_dialog_injects_persona_brief(self):
        # 主链注入：保存 persona 后，dialog system 含 [Persona]（0.22 教训：
        # 必须有 planner 级测试锁定注入链，技能级单测发现不了静默失效）
        st, _ = self._save(self.FULL)
        self.assertEqual(st, 200)
        self.llm.reset([TEXT_REPLY % "好的，小玲。"])
        st, out = _post(self.port, "/api/dialog", {
            "text": "你好", "learner_id": self.LEARNER,
            "conversation_id": "conv-pers", "native_lang": "en"})
        self.assertEqual(st, 200)
        system = self.llm.calls[0][0]
        self.assertEqual(system["role"], "system")
        self.assertIn("[Persona]", system["content"])
        self.assertIn("Address me as: Ling", system["content"])
        self.assertIn("Your identity: my practice buddy", system["content"])
        self.assertIn("takes precedence over the [Scene] role",
                      system["content"])
        # intervention cap 来自 persona（保存值 5）
        self.assertEqual(out["intervention"]["cap"], 5)

    def test_persona_cap_zero_silences_help(self):
        # cap=0：persona 打断上限压过求助 block（确定性执行，不靠 LLM 自觉）
        st, _ = self._save(dict(self.FULL, interrupt_cap=0))
        self.assertEqual(st, 200)
        self.llm.reset([TEXT_REPLY % "……"])
        st, out = _post(self.port, "/api/dialog", {
            "text": "苹果很多怎么说？", "learner_id": self.LEARNER,
            "conversation_id": "conv-cap0"})
        self.assertEqual(st, 200)
        self.assertEqual(out["intervention"]["level"], "none")
        self.assertEqual(out["intervention"]["reason"], "cap")
        self.assertEqual(out["intervention"]["cap"], 0)

    def test_unsaved_persona_absent_from_system(self):
        # 未保存过 persona 的 learner：system 不含 [Persona]（默认回退）
        fresh = self.LEARNER + "_fresh"
        self.llm.reset([TEXT_REPLY % "你好。"])
        st, out = _post(self.port, "/api/dialog", {
            "text": "你好", "learner_id": fresh,
            "conversation_id": "conv-fresh"})
        self.assertEqual(st, 200)
        system = self.llm.calls[0][0]["content"]
        self.assertNotIn("[Persona]", system)
        self.assertEqual(out["intervention"]["cap"], 3)   # DEFAULT_CAP


if __name__ == "__main__":
    unittest.main(verbosity=2)

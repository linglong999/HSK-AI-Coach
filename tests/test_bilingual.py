# ============================================================
# 0.21 教学语言分层（英壳+中例句）回归测试
# 覆盖：
#   - fallback_reply 双语降级文案（非 zh → 英文）
#   - planner：_build_system 英文全局规则 / EN 解析重试喂回 / 语言注入技能参数
#   - explainer：EN 双 prompt（英壳+中例句）/ 英文降级模板（中文修正句保留）
#   - verifier：pass/partial/fail 反馈双语（中文要点保留）
#   - serve：/api/dialog native_lang 传参 + 回落 Router.native_lang
# 运行: python -m unittest tests.test_bilingual -v
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

from engine.explainer import Explainer, _fallback_explanation
from engine.graph.error_graph import ErrorGraph
from engine.router import Router
from engine.serve import make_handler
from engine.verifier import Verifier
from planner.fallback import fallback_reply
from planner.loop import Planner
from skills import build_registry


# ---------------- fallback_reply 双语 ----------------

class FallbackBilingualTest(unittest.TestCase):

    def test_zh_default(self):
        r = fallback_reply("parse")
        self.assertIn("换种说法", r["text"])

    def test_en_when_native_lang_non_zh(self):
        for lang in ("en", "EN", "vi"):
            r = fallback_reply("parse", native_lang=lang)
            self.assertIn("rephrase", r["text"], msg=f"native_lang={lang}")

    def test_all_reasons_have_en_variants(self):
        for reason in ("parse", "max_steps", "skill_error"):
            r = fallback_reply(reason, native_lang="en")
            self.assertNotIn("我", r["text"])
            self.assertTrue(r["text"].strip())

    def test_unknown_reason_defaults_to_parse_copy(self):
        r = fallback_reply("whatever", native_lang="en")
        self.assertEqual(r["text"], fallback_reply("parse", native_lang="en")["text"])


# ---------------- planner：英文规则 / 重试喂回 / 语言注入 ----------------

class PlannerLangTest(unittest.TestCase):

    def setUp(self):
        # 假引擎注入（隔离 .env 真实 Key：单测绝不打真实 LLM——0.20 教训）
        self.explainer = mock.MagicMock()
        self.explainer.explain.return_value = {
            "explanation": "en shell", "key_points": [{"id": "kp-1", "text": "x"}],
            "keywords": [], "uncertain_note": "", "free_generated": False}
        self.generation = mock.MagicMock()
        self.generation.generate_unit.return_value = {
            "ok": True, "status": "ok", "unit": {"id": "u-1"}, "attempts": 1}
        self.reg = build_registry(graph=ErrorGraph("bilingual_plan"),
                                  explainer=self.explainer,
                                  generation=self.generation)

    def test_build_system_en_rules(self):
        p = Planner(self.reg, llm_call=lambda m: "[]")
        sysmsg = p._build_system("", native_lang="en")
        self.assertIn("HSK Chinese tutor", sysmsg)
        self.assertIn("LANGUAGE POLICY", sysmsg)
        self.assertNotIn("你是 HSK 中文学习助教", sysmsg)
        # 技能清单仍拼接（语言切换不丢技能上下文）
        self.assertIn("identify_errors", sysmsg)

    def test_build_system_zh_default(self):
        p = Planner(self.reg, llm_call=lambda m: "[]")
        sysmsg = p._build_system("")
        self.assertIn("你是 HSK 中文学习助教", sysmsg)
        self.assertNotIn("LANGUAGE POLICY", sysmsg)

    def test_profile_header_language_follows(self):
        p = Planner(self.reg, llm_call=lambda m: "[]")
        self.assertIn("[Learner profile]", p._build_system("摘要", native_lang="en"))
        self.assertIn("【学习者画像】", p._build_system("摘要"))

    def test_parse_failure_gives_en_fallback(self):
        # JSON 残片（不以 [ 开头超过 10 字符才走裸文本兜底，这里用残片强制 fallback）
        p = Planner(self.reg, llm_call=lambda m: '[{"type": "text", "content": "截断')
        r = p.run("test", native_lang="en")
        self.assertTrue(r["fallback"])
        self.assertIn("rephrase", r["text"])

    def test_parse_retry_message_is_english(self):
        # 第一次坏输出 → 喂回英文 [format_error]；第二次合法 → 正常收尾
        seen_retry = {}

        def llm(messages):
            if not any(str(m.get("content", "")).startswith("[format_error]")
                       for m in messages):
                return "not json at all, just words"
            seen_retry["msg"] = str(messages[-1]["content"])
            return '[{"type":"text","content":"recovered"}]'

        p = Planner(self.reg, llm_call=llm)
        r = p.run("test", native_lang="en")
        self.assertFalse(r["fallback"])
        self.assertIn("Re-output strictly", seen_retry["msg"])

    def test_lang_injected_into_explain_error_params(self):
        # 语言是系统约束：native_lang=en → explain_error 参数被确定性注入
        def llm(messages):
            if not any(str(m.get("content", "")).startswith("[tool_result]")
                       for m in messages):
                return json.dumps([{
                    "type": "action", "name": "explain_error",
                    "params": {"error": {
                        "sentence": "我想买苹果很多。", "fragment": "苹果很多",
                        "correction": "很多苹果", "type": "语法-语序",
                        "knowledge_point_id": "kp-order-many"}},
                }], ensure_ascii=False)
            return '[{"type":"text","content":"done"}]'

        p = Planner(self.reg, llm_call=llm)
        r = p.run("why is this wrong", native_lang="en")
        self.assertFalse(r["fallback"])
        # trace.params 即下发参数：注入生效
        t = next(t for t in r["trace"] if t["name"] == "explain_error")
        self.assertEqual(t["params"].get("native_lang"), "en")
        # 引擎侧实收（假 explainer 捕获）：语言确实传到了讲解引擎
        self.assertEqual(self.explainer.explain.call_args.kwargs.get("native_lang"),
                         "en")

    def test_no_injection_when_native_lang_empty(self):
        def llm(messages):
            if not any(str(m.get("content", "")).startswith("[tool_result]")
                       for m in messages):
                return json.dumps([{
                    "type": "action", "name": "explain_error",
                    "params": {"error": {
                        "sentence": "我想买苹果很多。", "fragment": "苹果很多",
                        "correction": "很多苹果", "type": "语法-语序",
                        "knowledge_point_id": "kp-order-many"}},
                }], ensure_ascii=False)
            return '[{"type":"text","content":"done"}]'

        p = Planner(self.reg, llm_call=llm)
        r = p.run("这句话为什么错")
        t = next(t for t in r["trace"] if t["name"] == "explain_error")
        self.assertNotIn("native_lang", t["params"])
        self.assertEqual(self.explainer.explain.call_args.kwargs.get("native_lang"),
                         "")

    def test_generate_unit_context_lang_injected(self):
        # EN 模式：generate_unit 的 context 注入 self_language + language_directive
        def llm(messages):
            if not any(str(m.get("content", "")).startswith("[tool_result]")
                       for m in messages):
                return json.dumps([{
                    "type": "action", "name": "generate_unit",
                    "params": {"unit_type": "explain",
                               "context": {"for_keypoint": "把字句"}},
                }], ensure_ascii=False)
            return '[{"type":"text","content":"done"}]'

        p = Planner(self.reg, llm_call=llm)
        r = p.run("explain this point", native_lang="en")
        t = next(t for t in r["trace"] if t["name"] == "generate_unit")
        ctx = t["params"]["context"]
        self.assertEqual(ctx.get("self_language"), "en")
        self.assertIn("English", ctx.get("language_directive", ""))
        self.assertEqual(ctx.get("for_keypoint"), "把字句")
        # 引擎侧实收（假 generation 捕获）：语言字段传到了生成引擎
        eng_ctx = self.generation.generate_unit.call_args.args[1]
        self.assertEqual(eng_ctx.get("self_language"), "en")
        self.assertIn("English", eng_ctx.get("language_directive", ""))

    def test_generate_unit_explicit_directive_preserved(self):
        # LLM 已显式给 language_directive → 系统不覆盖（只补缺）
        def llm(messages):
            if not any(str(m.get("content", "")).startswith("[tool_result]")
                       for m in messages):
                return json.dumps([{
                    "type": "action", "name": "generate_unit",
                    "params": {"unit_type": "explain",
                               "context": {"for_keypoint": "把字句",
                                           "language_directive": "keep mine"}},
                }], ensure_ascii=False)
            return '[{"type":"text","content":"done"}]'

        p = Planner(self.reg, llm_call=llm)
        r = p.run("explain this point", native_lang="en")
        t = next(t for t in r["trace"] if t["name"] == "generate_unit")
        self.assertEqual(t["params"]["context"]["language_directive"], "keep mine")
        self.assertEqual(t["params"]["context"]["self_language"], "en")
        eng_ctx = self.generation.generate_unit.call_args.args[1]
        self.assertEqual(eng_ctx.get("language_directive"), "keep mine")

    def test_generate_unit_zh_not_injected(self):
        # zh 是引擎默认：context 保持原样（无 self_language/language_directive）
        def llm(messages):
            if not any(str(m.get("content", "")).startswith("[tool_result]")
                       for m in messages):
                return json.dumps([{
                    "type": "action", "name": "generate_unit",
                    "params": {"unit_type": "explain",
                               "context": {"for_keypoint": "把字句"}},
                }], ensure_ascii=False)
            return '[{"type":"text","content":"done"}]'

        p = Planner(self.reg, llm_call=llm)
        r = p.run("给我讲讲", native_lang="zh")
        t = next(t for t in r["trace"] if t["name"] == "generate_unit")
        ctx = t["params"]["context"]
        self.assertNotIn("self_language", ctx)
        self.assertNotIn("language_directive", ctx)
        eng_ctx = self.generation.generate_unit.call_args.args[1]
        self.assertNotIn("self_language", eng_ctx)
        self.assertNotIn("language_directive", eng_ctx)


# ---------------- explainer：英壳 + 中例句 ----------------

class ExplainerLangTest(unittest.TestCase):

    ERROR = {
        "sentence": "我想买苹果很多。", "fragment": "苹果很多",
        "correction": "很多苹果", "type": "语法-语序",
        "knowledge_point_id": "kp-order-many",
    }

    def _capturing_client(self):
        cap = {"system": None, "user": None}

        def chat_json_strict(system, user, temperature=0.4):
            cap["system"], cap["user"] = system, user
            return {
                "explanation": "The modifier goes before the noun: 很多苹果.",
                "key_points": [{"id": "kp-1", "text": "很多 precedes the noun"}],
                "keywords": ["语序"], "uncertain_note": "", "free_generated": False,
            }

        client = mock.MagicMock()
        client.chat_json_strict.side_effect = chat_json_strict
        return client, cap

    def test_en_prompts_used_when_native_lang_en(self):
        client, cap = self._capturing_client()
        Explainer(client=client).explain(self.ERROR, native_lang="en")
        self.assertIn("LANGUAGE RULE", cap["system"])
        self.assertIn("English explanation shell", cap["system"])
        self.assertIn("Original sentence", cap["user"])

    def test_zh_prompts_default(self):
        client, cap = self._capturing_client()
        Explainer(client=client).explain(self.ERROR)
        self.assertIn("费曼", cap["system"])
        self.assertIn("原句", cap["user"])

    def test_fallback_explanation_en_keeps_chinese_correction(self):
        out = _fallback_explanation(self.ERROR, native_lang="en")
        self.assertIn("more idiomatic Chinese way", out["explanation"])
        self.assertIn("很多苹果", out["explanation"])   # 中文修正句保留
        self.assertTrue(out["_degraded"])

    def test_fallback_explanation_zh_default(self):
        out = _fallback_explanation(self.ERROR)
        self.assertIn("很多苹果", out["explanation"])
        self.assertIn("不太对", out["explanation"])


# ---------------- verifier：反馈双语（中文要点保留） ----------------

class VerifierLangTest(unittest.TestCase):

    KP = [{"id": "kp-1", "text": "很多要放在名词前面"},
          {"id": "kp-2", "text": "形容词放在名词前"}]

    def _verifier(self, judgements):
        ver = Verifier(client=mock.MagicMock(), graph=None)
        ver.client.chat_json_strict.return_value = {
            "point_judgements": judgements, "flag_flowery_but_empty": False}
        return ver

    def test_pass_feedback_en(self):
        ver = self._verifier([{"id": "kp-1", "text": "很多要放在名词前面",
                               "is_covered": True, "evidence": "x"},
                              {"id": "kp-2", "text": "形容词放在名词前",
                               "is_covered": True, "evidence": "y"}])
        r = ver.verify("讲解", self.KP, "很多苹果", commit_graph=False,
                       native_lang="en")
        self.assertIn("Good. You covered", r["feedback"])

    def test_partial_feedback_en_keeps_chinese_points(self):
        # 覆盖一半（ratio=0.5 < 0.8 且 > 0）→ partial：英文引导 + 中文要点保留
        ver = self._verifier([{"id": "kp-1", "text": "很多要放在名词前面",
                               "is_covered": True, "evidence": "x"},
                              {"id": "kp-2", "text": "形容词放在名词前",
                               "is_covered": False, "evidence": ""}])
        r = ver.verify("讲解", self.KP, "苹果很好吃", commit_graph=False,
                       native_lang="en")
        self.assertIn("Think about how to say these in Chinese", r["feedback"])
        self.assertIn("形容词放在名词前", r["feedback"])  # 中文要点保留

    def test_fail_feedback_en(self):
        # 全部未覆盖（ratio=0）→ fail
        ver = self._verifier([{"id": "kp-1", "text": "很多要放在名词前面",
                               "is_covered": False, "evidence": ""},
                              {"id": "kp-2", "text": "形容词放在名词前",
                               "is_covered": False, "evidence": ""}])
        r = ver.verify("讲解", self.KP, "完全无关", commit_graph=False,
                       native_lang="en")
        self.assertIn("simpler angle", r["feedback"])

    def test_feedback_zh_default(self):
        ver = self._verifier([{"id": "kp-1", "text": "很多要放在名词前面",
                               "is_covered": True, "evidence": "x"}])
        r = ver.verify("讲解", self.KP, "很多苹果", commit_graph=False)
        self.assertIn("理解到位", r["feedback"])


# ---------------- serve：native_lang 传参与回落 ----------------

class _FakeRecognizer:
    def recognize(self, text, level=3, native_lang=""):
        return {"errors": [], "uncertain": [], "degraded": []}


class _FakeRouter:
    learner_id = "bilingual_user"

    def __init__(self, native_lang=""):
        self.graph = ErrorGraph("bilingual_serve")
        self.recognizer = _FakeRecognizer()
        self.native_lang = native_lang


def _start_server(router, dialog_llm):
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html>x</html>")
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", port),
        make_handler(router, tmp, dialog_llm=dialog_llm, memory_root=tmp))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _post(port, body):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", "/api/dialog", json.dumps(body).encode(),
              {"Content-Type": "application/json"})
    r = c.getresponse()
    raw = r.read()
    c.close()
    return r.status, json.loads(raw.decode())


class ServeLangTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # 一直坏输出的 LLM：native_lang=en → 英文 fallback 文案可观测
        cls.llm = lambda messages: '[{"type": "text", "content": "trunc'
        cls.httpd, cls.port = _start_server(_FakeRouter(), dialog_llm=cls.llm)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_payload_native_lang_en_gives_en_fallback(self):
        st, out = _post(self.port, {"text": "我想买苹果很多。",
                                    "native_lang": "en"})
        self.assertEqual(st, 200)
        self.assertTrue(out.get("fallback"))
        self.assertIn("rephrase", out.get("text", ""))

    def test_default_still_chinese(self):
        st, out = _post(self.port, {"text": "我想买苹果很多。"})
        self.assertEqual(st, 200)
        self.assertIn("换种说法", out.get("text", ""))

    def test_falls_back_to_router_native_lang(self):
        # 注意 type(self).llm：实例访问 self.llm 会把函数类属性绑定为 bound method
        # （多出隐式 self 实参 → planner 调用 TypeError），必须类访问取原函数
        httpd, port = _start_server(_FakeRouter(native_lang="en"),
                                    dialog_llm=type(self).llm)
        try:
            st, out = _post(port, {"text": "我想买苹果很多。"})
            self.assertEqual(st, 200, msg=str(out))
            self.assertIn("rephrase", out.get("text", ""))
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

# ============================================================
# tests/test_intervention.py
# 0.22 方向3 · 介入时机 纯函数 + serve 主链集成测试
# 覆盖（设计稿 §3.1 / D3.1-D3.4）：
#   - decide_intervention 组合信号分档（help/unclear/empty/streak/cap/upgrade/fluent）
#   - detect_help_intent 宁漏勿错（meta 求助 vs 场景产出）
#   - InterventionTracker 状态机（打断计数 / 近错窗口 / 求助句不入窗口）
#   - build_intervention_directive 双语注入段 + 预扫结果注入规则
#   - serve 主链：预扫喂图谱+账本、intervention 响应字段、
#     单次高置信偏误静默记录（任务型教学核心：能交流不打断）
# 运行: python -m unittest tests.test_intervention -v
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
from engine.intervention import (
    DEFAULT_CAP, detect_help_intent, decide_intervention,
    InterventionTracker, build_intervention_directive)
from engine.serve import make_handler


# ---------------- 纯函数：主动求助（meta vs 场景） ----------------

class DetectHelpIntentTest(unittest.TestCase):

    def test_zh_help_phrases(self):
        for s in ["苹果很多怎么说？", "‘把’和‘被’有什么区别？",
                  "这句话有错吗", "帮我改一下这句", "这样说自然吗"]:
            self.assertTrue(detect_help_intent(s), s)

    def test_en_help_phrases(self):
        for s in ["How do I say 'thank you'?",
                  "is it correct?",
                  "what's the difference between 了 and 过?",
                  "please translate this", "any mistakes here?"]:
            self.assertTrue(detect_help_intent(s), s)

    def test_scene_utterances_not_help(self):
        # 场景产出句不命中——宁漏勿错（打断上限内宁可不介入）
        for s in ["帮我看看菜单", "坐这里对吗", "我想买很多苹果",
                  "help me carry this", "你好", ""]:
            self.assertFalse(detect_help_intent(s), s)


# ---------------- 纯函数：介入分档 ----------------

class DecideInterventionTest(unittest.TestCase):

    def test_help_blocks(self):
        self.assertEqual(
            decide_intervention("这个怎么说？"), ("block", "help"))

    def test_unclear_blocks(self):
        # 含义不清：识别置信低于阈值 → 阻断讲解
        self.assertEqual(
            decide_intervention("呃那个。。。",
                                max_conf=0.3), ("block", "unclear"))

    def test_empty_is_light(self):
        # 去标点后内容字符 <2 → 犹豫空句 → 轻介入
        self.assertEqual(
            decide_intervention("嗯…"), ("light", "empty"))

    def test_two_char_not_empty(self):
        # "你好"两字不算空；无错高置信 → 流利静默
        self.assertEqual(
            decide_intervention("你好", max_conf=1.0), ("none", "fluent"))

    def test_streak_is_light(self):
        # 近 5 句 ≥3 错 → 轻介入（计数制）
        self.assertEqual(
            decide_intervention("我想买苹果很多。", max_conf=0.9,
                                recent_error_flags=[True, True, True]),
            ("light", "streak"))

    def test_two_errors_no_streak(self):
        # 不足 3 错不触发（防单句误伤升级）
        self.assertEqual(
            decide_intervention("我想买苹果很多。", max_conf=0.9,
                                recent_error_flags=[True, True]),
            ("none", "fluent"))

    def test_upgrade_after_light(self):
        # 轻介入后下一句仍在挣扎 → 升阻断
        self.assertEqual(
            decide_intervention("嗯…", last_level="light"),
            ("block", "upgrade"))
        self.assertEqual(
            decide_intervention("我想买苹果很多。", max_conf=0.9,
                                recent_error_flags=[True, True, True],
                                last_level="light"),
            ("block", "upgrade"))

    def test_cap_has_priority(self):
        # 打断上限优先——用满后即使求助也静默
        self.assertEqual(
            decide_intervention("这个怎么说？", interrupt_used=3, cap=3),
            ("none", "cap"))

    def test_confident_single_error_stays_silent(self):
        # 任务型教学核心语义：单次高置信偏误 = 表达可懂 → 静默记录
        self.assertEqual(
            decide_intervention("我想买苹果很多。", max_conf=0.9),
            ("none", "fluent"))


# ---------------- Tracker 状态机 ----------------

class InterventionTrackerTest(unittest.TestCase):

    def test_help_does_not_enter_flags_window(self):
        tr = InterventionTracker()
        # 求助句 error_flag=None → 不入近错窗口
        tr.observe("这个怎么说？", error_flag=None)
        self.assertEqual(tr.flags, [])

    def test_streak_light_then_upgrade_block(self):
        tr = InterventionTracker()
        levels = []
        for _ in range(4):
            lvl, _ = tr.observe("我想买苹果很多。", max_conf=0.9,
                                error_flag=True)
            levels.append(lvl)
        # 轮1-2 不足3错 → none；轮3 streak → light；轮4 仍在挣扎 → upgrade block
        self.assertEqual(levels, ["none", "none", "light", "block"])
        self.assertEqual(tr.interrupt_used, 2)   # light+block 各计 1

    def test_cap_zero_mutes_everything(self):
        tr = InterventionTracker()
        lvl, reason = tr.observe("这个怎么说？", cap=0)
        self.assertEqual((lvl, reason), ("none", "cap"))
        self.assertEqual(tr.interrupt_used, 0)


# ---------------- 注入段（双语 + 预扫结果） ----------------

class InterventionDirectiveTest(unittest.TestCase):

    def test_zh_three_levels(self):
        none_d = build_intervention_directive("none", "fluent", "zh",
                                              recognition={"errors":
                                                           [{"fragment": "奶茶"}]})
        self.assertIn("自然重述", none_d)
        # P0.19 N7①：单错误 -> 只 recast 不再点名；多错误才至多轻点最关键的 1 个
        self.assertIn("重述即已改正，不必再点名", none_d)
        self.assertIn("至多只轻点最关键的 1 个", none_d)
        self.assertNotIn("明确点出", none_d)
        self.assertNotIn("每个错误", none_d)
        clean_d = build_intervention_directive("none", "fluent", "zh")
        self.assertIn("未识别到明显偏误", clean_d)
        light_d = build_intervention_directive("light", "streak", "zh")
        self.assertIn("轻介入", light_d)
        self.assertIn("引导", light_d)
        block_d = build_intervention_directive("block", "help", "zh")
        self.assertIn("直接回应", block_d)

    def test_en_three_levels(self):
        none_d = build_intervention_directive("none", "fluent", "en",
                                              recognition={"errors":
                                                           [{"fragment": "奶茶"}]})
        self.assertIn("natural flow", none_d)
        self.assertIn("recast", none_d)
        # P0.19 N7①：单错误不点名；多错误才轻点最关键的 1 个
        self.assertIn("do not flag it again", none_d)
        self.assertIn("the 1 most important one", none_d)
        self.assertNotIn("EACH error", none_d)
        self.assertNotIn("leaving none out", none_d)
        light_d = build_intervention_directive("light", "streak", "en")
        self.assertIn("light touch", light_d)
        block_d = build_intervention_directive("block", "unclear", "en")
        self.assertIn("step in", block_d)

    def test_priority_over_rule3(self):
        # 段头必须声明优先于全局规则3（技能选用建议）
        for lvl in ("none", "light", "block"):
            d = build_intervention_directive(lvl, "fluent", "zh")
            self.assertIn("优先于全局规则3", d)

    def test_recognition_injected_only_when_light_or_block(self):
        rec = {"errors": [{"fragment": "苹果很多", "correction": "很多苹果",
                           "type": "语法-语序", "confidence": 0.9}], }
        # light/block 注入识别结果；none 不注入（静默语义）
        self.assertIn("苹果很多",
                      build_intervention_directive("light", "streak", "zh",
                                                   recognition=rec))
        self.assertIn("苹果很多",
                      build_intervention_directive("block", "help", "zh",
                                                   recognition=rec))
        self.assertNotIn("苹果很多",
                         build_intervention_directive("none", "fluent", "zh",
                                                      recognition=rec))

    def test_unscanned_directive_hides_recognition(self):
        # 未预扫（求助句/材料）→ 不提"识别已在后台完成"
        d = build_intervention_directive("block", "help", "zh")
        self.assertNotIn("识别已在后台完成", d)

    def test_scanned_directive_suppresses_reidentify(self):
        d = build_intervention_directive("light", "streak", "zh",
                                         recognition={"errors": []})
        self.assertIn("无需再调 identify_errors", d)

    def test_invalid_level_returns_empty(self):
        self.assertEqual(build_intervention_directive("", "fluent", "zh"), "")
        self.assertEqual(
            build_intervention_directive("hard", "fluent", "zh"), "")


# ---------------- serve 主链集成 ----------------

class FakeRecognizer:
    """含「苹果很多」片段返回固定偏误；其余干净零命中（宁漏勿错）。"""

    def recognize(self, text, level=3, native_lang=""):
        if "苹果很多" not in str(text or ""):
            return {"errors": [], "uncertain": [], "degraded": []}
        return {"errors": [{
            "fragment": "苹果很多", "correction": "很多苹果",
            "type": "语法-语序", "type_confident": True, "confidence": 0.9,
            "knowledge_point_id": "kp-order-many", "uncertain": False,
        }], "uncertain": [], "degraded": []}


class CaptureLLM:
    """脚本化 llm_call：记录 system（注入断言），按序返回响应。"""

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


IDENTIFY_ACTION = ('[{"type":"action","name":"identify_errors",'
                   '  "params":{"text":"我想买苹果很多。"}}]')
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
        f.write("<html>s3</html>")
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


class ServeInterventionTest(unittest.TestCase):
    """主链集成：预扫喂图谱+账本 → 分档 → 注入 → 响应字段。"""

    LEARNER = "s3_intv"

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

    def _dialog(self, text, conv, native_lang=""):
        return _post(self.port, "/api/dialog", {
            "text": text, "learner_id": self.LEARNER,
            "conversation_id": conv, "native_lang": native_lang})

    def test_fluent_error_recorded_silently(self):
        # 单次高置信偏误：静默记录（none/fluent），但预扫照常喂图谱+账本
        self.llm.reset([IDENTIFY_ACTION, TEXT_REPLY % "已记下。"])
        st, out = self._dialog("我想买苹果很多。", "conv-fluent")
        self.assertEqual(st, 200)
        self.assertEqual(out["intervention"]["level"], "none")
        self.assertEqual(out["intervention"]["reason"], "fluent")
        self.assertEqual(out["intervention"]["used"], 0)
        self.assertEqual(out["intervention"]["cap"], DEFAULT_CAP)
        # 预扫 + planner 同句：账本仅 1 条（skip_text 去重）
        ledger = ErrorLedger(self.LEARNER, root=self.root)
        self.assertEqual(ledger.repeated_count("kp-order-many"), 1)
        # system 注入 [Intervention] 段（静默指令）
        system = self.llm.calls[0][0]
        self.assertEqual(system["role"], "system")
        self.assertIn("[Intervention]", system["content"])
        # N8：自然承接收尾，不向学习者披露"后台记录"（静默记录经账本/图谱呈现，见上 ledger 断言）
        self.assertIn("这个点稍后会再练到", system["content"])
        self.assertNotIn("静默记录", system["content"])

    def test_help_blocks_without_prescan(self):
        # 求助句：不预扫（识别交 planner 按需），直接阻断讲解
        self.llm.reset([TEXT_REPLY % "这是‘很多苹果’的语序。"])
        st, out = self._dialog("苹果很多怎么说？", "conv-help")
        self.assertEqual(st, 200)
        self.assertEqual(out["intervention"]["level"], "block")
        self.assertEqual(out["intervention"]["reason"], "help")
        system = self.llm.calls[0][0]["content"]
        self.assertIn("直接回应", system)
        self.assertNotIn("识别已在后台完成", system)   # 未预扫

    def test_streak_light_then_upgrade(self):
        # 同会话 4 轮偏误句：轮3 light（近5句≥3错），轮4 升级 block
        # 账本按 learner 共享（非 conversation）→ 用相对增量断言
        before = ErrorLedger(
            self.LEARNER, root=self.root).repeated_count("kp-order-many")
        levels = []
        for i in range(4):
            self.llm.reset([IDENTIFY_ACTION, TEXT_REPLY % f"第{i+1}轮。"])
            st, out = self._dialog("我想买苹果很多。", "conv-streak")
            self.assertEqual(st, 200)
            levels.append(out["intervention"]["level"])
        self.assertEqual(levels, ["none", "none", "light", "block"])
        after = ErrorLedger(
            self.LEARNER, root=self.root).repeated_count("kp-order-many")
        self.assertEqual(after - before, 4)   # 每轮预扫各记 1 条

    def test_material_not_intervened(self):
        # 超长文本视为学习材料：不识别不分档
        long_text = "这是一段很长的学习材料。" * 30
        self.llm.reset([TEXT_REPLY % "材料已收到。"])
        st, out = self._dialog(long_text, "conv-mat")
        self.assertEqual(st, 200)
        self.assertEqual(out["intervention"]["level"], "none")
        self.assertEqual(out["intervention"]["reason"], "material")

    def test_en_directive_language(self):
        # 英文界面：介入段用英文指令
        self.llm.reset([TEXT_REPLY % "Got it."])
        st, out = self._dialog("How do I say 'a lot of apples'?",
                               "conv-en", native_lang="en")
        self.assertEqual(st, 200)
        self.assertEqual(out["intervention"]["level"], "block")
        system = self.llm.calls[0][0]["content"]
        self.assertIn("step in", system)


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
# tests/test_dialog_run_smoke.py —— dialog() 主链行为基线冒烟（深化批次 A）
# 目的：在【未改动】的 DialogService 上锁死现状行为，作为后续 dialog() 拆分的
#       行为基线（重构零行为改动的裁判）。覆盖：
#   1. happy path：响应结构全键 + 记忆两消息落盘 + 会话标题 + 账本 observation_error
#   2. 空输入 → 400
#   3. 游客额度满 → 429（LLM 不触达）
#   4. 材料句（超 MAX_SCAN_CHARS）→ 不预扫不分档（level=none/reason=material）
#   5. 多轮记忆注入：第二轮 planner 收到第一轮 user+assistant 历史
# 全部 fake（router/graph/recognizer/gate/llm），不起 HTTP、不触网；SQLite 落临时目录。

import json
import shutil
import tempfile
import unittest

from engine.dialog_service import DialogService
from engine.intervention import MAX_SCAN_CHARS
from engine.memory.error_ledger import ErrorLedger
from engine.memory.learner_memory import LearnerMemory

REPLY = "这句可以这样改：我想买很多苹果。"
USER_INPUT = "我想买苹果很多。"
KP_ID = "hsk30-g2-001"


class FakeGraph:
    def graph_snapshot(self):
        return {"nodes": []}

    def get_review_queue(self):
        return []

    def ingest_error(self, bias, event_key=""):
        return {"status": "ok", "kp_id": bias.get("knowledge_point_id", "")}

    def link_errors_in_sentence(self, kps, event_key=""):
        return None

    def save(self):
        return None


class FakeRecognizer:
    """恒定返回一个带 KP 的确认偏误（驱动预扫→账本→介入分档全链）。"""

    def recognize(self, text, level=3, native_lang=""):
        return {
            "errors": [{
                "fragment": "苹果很多",
                "correction": "很多苹果",
                "type": "语序-定语后置",
                "confidence": 0.9,
                "knowledge_point_id": KP_ID,
            }],
            "uncertain": [],
            "degraded": [],
            "hypotheses": [],
        }


class FakeRouter:
    learner_id = "smoke-learner"
    native_lang = "zh"

    def __init__(self):
        self.graph = FakeGraph()
        self.recognizer = FakeRecognizer()


class FakeGate:
    def __init__(self, ok):
        self.ok = ok

    def check_and_consume(self, vid):
        return self.ok, 0, "次日 UTC 00:00 重置"


class DialogSmokeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hsk_dialog_smoke_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.calls = []          # 每轮 planner 收到的 messages（含 system/history/user）
        self.router = FakeRouter()
        self.svc = DialogService(router=self.router, dialog_llm=self._llm,
                                 memory_root=self.tmp, gate=None)

    def _llm(self, messages):
        self.calls.append(messages)
        return json.dumps([{"type": "text", "content": REPLY}],
                          ensure_ascii=False)


class EmptyInputTest(DialogSmokeBase):
    def test_empty_text_400(self):
        status, out = self.svc.dialog({"text": "   "})
        self.assertEqual((status, out), (400, {"error": "empty text"}))
        self.assertEqual(self.calls, [])   # 未触 LLM


class VisitorQuotaTest(DialogSmokeBase):
    def test_quota_exhausted_429_llm_untouched(self):
        svc = DialogService(router=self.router, dialog_llm=self._llm,
                            memory_root=self.tmp, gate=FakeGate(ok=False))
        status, out = svc.dialog({"text": "你好"}, vid="v-test")
        self.assertEqual(status, 429)
        self.assertEqual(out["code"], "visitor_quota_exceeded")
        self.assertEqual(out["remaining"], 0)
        self.assertEqual(self.calls, [])   # 满额直接拒，LLM 不触达


class HappyPathTest(DialogSmokeBase):
    def test_structure_and_persistence(self):
        status, out = self.svc.dialog({"text": USER_INPUT})
        self.assertEqual(status, 200)
        # ---- 响应结构（对外契约键全在） ----
        self.assertEqual(out["dialog_version"], "v1")
        self.assertEqual(out["learner_id"], "smoke-learner")
        self.assertEqual(out["conversation_id"], "default")
        self.assertIsNone(out["provider"])          # mock 注入路径 → 无真实供应商
        self.assertEqual(out["text"], REPLY)
        self.assertEqual(out["used_skills"], [])
        self.assertEqual(out["steps"], 1)
        self.assertFalse(out["fallback"])
        self.assertEqual(out["trace"], [])
        self.assertEqual(out["why"], [])             # mock 路径 provider=None → 不生成 why
        self.assertEqual(out["degraded"], [])        # 全 fake 写入成功，无降级
        self.assertEqual(out["graph"], {"nodes": []})
        iv = out["intervention"]
        self.assertIn(iv["level"], ("none", "light", "block", "encourage"))
        self.assertIn("reason", iv)
        self.assertIn("used", iv)
        self.assertIn("cap", iv)
        # ---- 记忆：user+assistant 两消息落盘，assistant 带 0.27 成果卡 metadata ----
        mem = LearnerMemory("smoke-learner", root=self.tmp)
        msgs = mem.get_history("default")
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[0]["content"], USER_INPUT)
        self.assertEqual(msgs[1]["content"], REPLY)
        meta = msgs[1]["metadata"]
        for key in ("cards", "why", "skills", "fallback"):
            self.assertIn(key, meta)
        # ---- 会话标题：首条消息自动设题（截 18 字） ----
        sessions = mem.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["title"], USER_INPUT[:18])
        self.assertEqual(sessions[0]["message_count"], 2)
        # ---- 账本：预扫确认偏误 → observation_error 落盘 ----
        led = ErrorLedger(learner_id="smoke-learner", root=self.tmp)
        kinds = [(e.get("kind"), e.get("kp_id")) for e in led.recent()]
        self.assertIn(("observation_error", KP_ID), kinds)


class MaterialSentenceTest(DialogSmokeBase):
    def test_too_long_text_skips_scan_and_tiering(self):
        status, out = self.svc.dialog(
            {"text": "材" * (MAX_SCAN_CHARS + 1)})
        self.assertEqual(status, 200)
        self.assertEqual(out["text"], REPLY)
        iv = out["intervention"]
        self.assertEqual(iv["level"], "none")     # 材料句：不介入不分档
        self.assertEqual(iv["reason"], "material")
        self.assertEqual(iv["used"], 0)           # observe 未调用，不打断计数


class MultiRoundHistoryTest(DialogSmokeBase):
    def test_second_round_sees_first_round_history(self):
        self.svc.dialog({"text": "第一句。", "conversation_id": "c1"})
        self.svc.dialog({"text": "第二句。", "conversation_id": "c1"})
        self.assertEqual(len(self.calls), 2)
        # 第二轮 messages：system + [user1, assistant1] + user2
        msgs2 = self.calls[1]
        self.assertEqual(msgs2[0]["role"], "system")
        pairs = [(m["role"], m["content"]) for m in msgs2]
        self.assertIn(("user", "第一句。"), pairs)
        self.assertIn(("assistant", REPLY), pairs)
        self.assertEqual(msgs2[-1], {"role": "user", "content": "第二句。"})


if __name__ == "__main__":
    unittest.main()

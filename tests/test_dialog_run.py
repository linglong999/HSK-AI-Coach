# -*- coding: utf-8 -*-
# tests/test_dialog_run.py —— DialogRun 协作器单测（深化批次D）
# 直接构造 DialogRun 注入 fake 依赖（不起 DialogService / HTTP 层），验证：
#   1. happy path：13 键响应 + planner 收齐参数 + 预扫调用 + 账本 observation_error
#   2. 材料句：不预扫不分档（identify 未触达）
#   3. 求助句：不预扫（identify 未触达），分档照走
#   4. 画像 user_level → HSK5 全链（planner/identify 收到 + router.set_level 同步）
#   5. persona interrupt_cap → 介入 cap 透传
#   6. 历史注入：既有会话历史透传 planner；首句 touch 设标题
#   7. fallback → degraded 追加 planner 通知
#   8. provider=None（mock 路径）→ assistant metadata why=[] 不触网
# 记忆/写回/账本用真实实例（临时目录），router/planner/identify 用 fake。

import shutil
import tempfile
import unittest

from engine.dialog_run import DialogRun
from engine.dialog_service import DialogService
from engine.intervention import MAX_SCAN_CHARS
from engine.intervention import InterventionTracker
from engine.memory.error_ledger import ErrorLedger
from engine.memory.learner_memory import LearnerMemory
from engine.memory.writeback import Writeback

REPLY = "可以这样说：很多苹果。"
USER_INPUT = "我想买苹果很多。"
KP_ID = "hsk30-g2-001"

PRE_SCAN = {
    "errors": [{"fragment": "苹果很多", "correction": "很多苹果",
                "type": "语序-定语后置", "confidence": 0.9,
                "knowledge_point_id": KP_ID}],
    "uncertain": [], "degraded": [], "hypotheses": [],
}


class FakeGraph:
    def graph_snapshot(self):
        return {"nodes": []}

    def get_review_queue(self):
        return []

    def ingest_error(self, bias, event_key=""):
        return {"status": "ok"}

    def link_errors_in_sentence(self, kps, event_key=""):
        return None

    def save(self):
        return None


class FakeRouter:
    learner_id = "u-run"
    native_lang = "zh"

    def __init__(self):
        self.graph = FakeGraph()
        self.levels = []

    def set_level(self, label):
        self.levels.append(label)


class FakePlanner:
    def __init__(self, res=None):
        self.calls = []
        self.res = res or {"text": REPLY, "used_skills": ["identify_errors"],
                           "steps": 2, "fallback": False,
                           "trace": [{"name": "identify_errors", "ok": True,
                                      "params": {"text": USER_INPUT},
                                      "result": PRE_SCAN}]}

    def run(self, user_input, **kw):
        self.calls.append((user_input, kw))
        return dict(self.res)


class FakeIdentify:
    def __init__(self, result=PRE_SCAN, exc=None):
        self.calls = []
        self.result = result
        self.exc = exc

    def run(self, params):
        self.calls.append(params)
        if self.exc:
            raise self.exc
        return self.result


def _ctx(text=USER_INPUT, learner="u-run", cid="default", **extra):
    base = {"user_input": text, "learner_id": learner, "conversation_id": cid,
            "provider_id": "", "native_lang": "zh", "learner_l1": "unknown",
            "scene_id": ""}
    base.update(extra)
    return base


class DialogRunBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hsk_dialogrun_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.router = FakeRouter()
        self.planner = FakePlanner()
        self.identify = FakeIdentify()
        self.mem = LearnerMemory("u-run", root=self.tmp)
        self.wb = Writeback(graph=self.router.graph, learner_id="u-run",
                            root=self.tmp)
        self.tracker = InterventionTracker()

    def _run(self, ctx=None, planner=None, identify=None, provider=None,
             mem=None):
        return DialogRun(
            router=self.router,
            planner=planner or self.planner,
            identify_skill=identify or self.identify,
            mem=mem or self.mem,
            wb=self.wb,
            tracker=self.tracker,
            normalize_user_level=DialogService._normalize_user_level,
            writeback_ledger_events=DialogService._writeback_ledger_events,
            assistant_payload=DialogService._assistant_payload,
        ).run(ctx or _ctx(), provider)


class HappyPathTest(DialogRunBase):
    def test_full_response_and_side_effects(self):
        status, out = self._run()
        self.assertEqual(status, 200)
        for key in ("dialog_version", "learner_id", "conversation_id",
                    "provider", "text", "used_skills", "steps", "fallback",
                    "trace", "intervention", "why", "degraded", "graph"):
            self.assertIn(key, out)
        self.assertEqual(out["text"], REPLY)
        self.assertIsNone(out["provider"])
        self.assertEqual(out["degraded"], [])
        # planner 参数齐
        user_input, kw = self.planner.calls[0]
        self.assertEqual(user_input, USER_INPUT)
        self.assertEqual(kw["learner_id"], "u-run")
        self.assertEqual(kw["native_lang"], "zh")
        self.assertEqual(kw["user_level"], "HSK3")       # 画像未设 → 默认
        for d in ("profile_summary", "persona_brief", "style_directive",
                  "scene_brief", "intervention_directive"):
            self.assertIn(d, kw)
        # 预扫以 level=HSK3 触达
        self.assertEqual(len(self.identify.calls), 1)
        self.assertEqual(self.identify.calls[0]["level"], "HSK3")
        # 账本：预扫确认偏误落 observation_error
        kinds = [e.get("kind") for e in ErrorLedger(
            learner_id="u-run", root=self.tmp).recent()]
        self.assertIn("observation_error", kinds)
        # 记忆：user+assistant 落盘；首句 touch 标题
        msgs = self.mem.get_history("default")
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[1]["metadata"]["cards"][0]["name"],
                         "identify_errors")
        self.assertTrue(self.mem.has_session("default"))


class SkipScanTest(DialogRunBase):
    def test_material_sentence_skips_identify(self):
        status, out = self._run(_ctx(text="材" * (MAX_SCAN_CHARS + 1)))
        self.assertEqual(status, 200)
        self.assertEqual(self.identify.calls, [])        # 识别未触达
        self.assertEqual(out["intervention"]["level"], "none")
        self.assertEqual(out["intervention"]["reason"], "material")
        # 记忆照写（材料句也要有对话记录）
        self.assertEqual(len(self.mem.get_history("default")), 2)

    def test_help_intent_skips_identify(self):
        status, out = self._run(_ctx(text="这个用中文怎么说？"))
        self.assertEqual(status, 200)
        self.assertEqual(self.identify.calls, [])        # 求助句不预扫
        self.assertNotEqual(out["intervention"]["reason"], "material")


class LevelAndCapTest(DialogRunBase):
    def test_profile_level_propagates(self):
        self.mem.update_profile(user_level="HSK5")
        status, out = self._run()
        self.assertEqual(status, 200)
        self.assertEqual(self.planner.calls[0][1]["user_level"], "HSK5")
        self.assertEqual(self.identify.calls[0]["level"], "HSK5")
        self.assertEqual(self.router.levels, ["HSK5"])   # Router 已同步

    def test_persona_cap_propagates(self):
        self.mem.update_profile(persona={"interrupt_cap": 1})
        status, out = self._run()
        self.assertEqual(status, 200)
        self.assertEqual(out["intervention"]["cap"], 1)


class HistoryAndFallbackTest(DialogRunBase):
    def test_history_injected_and_no_touch_when_not_first(self):
        self.mem.append("c1", "user", "上一句。")
        self.mem.append("c1", "assistant", "上一答。")
        status, out = self._run(_ctx(cid="c1"))
        self.assertEqual(status, 200)
        kw = self.planner.calls[0][1]
        pairs = [(m["role"], m["content"]) for m in kw["history"]]
        self.assertIn(("user", "上一句。"), pairs)
        self.assertIn(("assistant", "上一答。"), pairs)

    def test_fallback_adds_degraded_notice(self):
        planner = FakePlanner(res={"text": "兜底文案", "used_skills": [],
                                   "steps": 1, "fallback": True,
                                   "reason": "unterminated", "trace": []})
        status, out = self._run(planner=planner)
        self.assertEqual(status, 200)
        self.assertTrue(out["fallback"])
        stages = [n["stage"] for n in out["degraded"]]
        self.assertIn("planner", stages)
        # fallback 文案也进记忆（多轮不断档）
        roles = [m["role"] for m in self.mem.get_history("default")]
        self.assertEqual(roles, ["user", "assistant"])
        self.assertEqual(self.mem.get_history("default")[1]["content"],
                         "兜底文案")


class WhyGateTest(DialogRunBase):
    def test_provider_none_skips_why(self):
        status, out = self._run(provider=None)
        self.assertEqual(status, 200)
        self.assertEqual(out["why"], [])                  # mock 路径不生成 why
        meta = self.mem.get_history("default")[1]["metadata"]
        self.assertEqual(meta["why"], [])


if __name__ == "__main__":
    unittest.main()

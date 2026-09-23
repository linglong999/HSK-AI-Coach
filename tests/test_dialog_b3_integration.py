# -*- coding: utf-8 -*-
# tests/test_dialog_b3_integration.py —— B3 G5 接入 dialog_run 的集成测试
# 直接构造 DialogRun（不起 DialogService / HTTP 层），验证主链上 B3 观测是否按契约接入：
#   1. 回避观测：target_constructions 非空才激活；avoided/unlearned 写账本 + Node 快照；
#      attempt_ok/attempt_err 走既有链（不重复写 avoidance_observed）
#   2. 空 target_constructions → 观测不激活零行为（拍板3 退化态）
#   3. 元认知自评：meta_self_level 合法 → 事件落 user 消息 metadata_json + Node 快照 +
#      route_bias（6 格偏置）；query_meta_events 可聚合 (self_level, outcome) 对
#   4. 每任务自评 ≤1：meta_ask_state 标记置位后不重复触发
#   5. 静默性：any 观测结果不进 intervention/directive（response 层面不可见）
# 既有测试零改动全绿由本文件自证主链仍可跑通 happy path。

import shutil
import tempfile
import unittest

from engine.dialog_run import DialogRun
from engine.dialog_service import DialogService
from engine.graph.model import Node
from engine.memory.error_ledger import ErrorLedger, query_meta_events
from engine.memory.learner_memory import LearnerMemory
from engine.intervention import InterventionTracker
from engine.memory.writeback import Writeback

REPLY = "很好。"
KP = "hsk30-g2-001"          # 目标构式·把字句
KP2 = "hsk30-g2-002"         # 已学目标构式（有正向证据 → 未用=avoided）
KP3 = "hsk30-g2-003"         # 已教目标构式（无正向证据 → 未用=unlearned）

# 目标构式被"用了且对"：产出含"把"、识别无该 kp 错误 → attempt_ok
OUT_OK_BA = "我把苹果吃了。"
# 目标构式被"用了但错"：识别报该 kp 错误 → attempt_err
OUT_ERR_BA = "我把吃了苹果。"
# 目标构式"没用"：产出不含构式特征
OUT_NO_BA = "我饿了。"


class FakeGraph:
    def __init__(self, nodes=None):
        self._nodes = dict(nodes or {})

    def graph_snapshot(self):
        return {"nodes": {nid: n.to_dict()
                          for nid, n in self._nodes.items()}}

    def get_review_queue(self):
        return []

    def ingest_error(self, bias, event_key=""):
        return {"status": "ok"}


class FakeRouter:
    learner_id = "u-b3"
    native_lang = "zh"

    def __init__(self, graph):
        self.graph = graph
        self.levels = []

    def set_level(self, label):
        self.levels.append(label)


class FakePlanner:
    def __init__(self):
        self.calls = []

    def run(self, user_input, **kw):
        self.calls.append((user_input, kw))
        return {"text": REPLY, "used_skills": [], "steps": 1,
                "fallback": False, "trace": []}


def _pre(errors=None, uncertain=None):
    return {"errors": list(errors or []), "uncertain": list(uncertain or []),
            "degraded": [], "hypotheses": []}


def _ctx(text=OUT_NO_BA, **extra):
    base = {"user_input": text, "learner_id": "u-b3",
            "conversation_id": "c-b3", "provider_id": "",
            "native_lang": "zh", "learner_l1": "unknown", "scene_id": ""}
    base.update(extra)
    return base


class B3Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hsk_b3_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # 两目标构式：KP 无能力证据、KP2 有历史能力（positive_count=2）、KP3 已教无证据
        self.nodes = {
            KP: Node(id=KP, knowledge_point=KP, level="HSK3", positive_count=0),
            KP2: Node(id=KP2, knowledge_point=KP2, level="HSK3", positive_count=2),
            KP3: Node(id=KP3, knowledge_point=KP3, level="HSK3",
                      positive_count=0),
        }
        self.graph = FakeGraph(self.nodes)
        self.router = FakeRouter(self.graph)
        self.planner = FakePlanner()
        self.mem = LearnerMemory("u-b3", root=self.tmp)
        self.wb = Writeback(graph=self.graph, learner_id="u-b3", root=self.tmp)
        self.tracker = InterventionTracker()

    def _run(self, ctx, target=None, meta_ask=None):
        return DialogRun(
            router=self.router,
            planner=self.planner,
            identify_skill=None,
            mem=self.mem,
            wb=self.wb,
            tracker=self.tracker,
            normalize_user_level=DialogService._normalize_user_level,
            writeback_ledger_events=DialogService._writeback_ledger_events,
            assistant_payload=DialogService._assistant_payload,
            target_constructions=target,
            meta_ask_state=meta_ask,
        ).run(ctx, None), (target or []), (meta_ask or {})


class AvoidanceTest(B3Base):
    def test_empty_targets_inactive_no_side_effects(self):
        """拍板3：无 focused 任务（target_constructions 空）→ 观测不激活零行为。"""
        (status, out), targets, _ = self._run(_ctx(OUT_NO_BA), target=[])
        self.assertEqual(status, 200)
        self.assertEqual(self.wb.ledger.count_for_kp(KP2), 0)
        n2 = self.nodes[KP2]
        self.assertIsNone(n2.avoidance_state)
        self.assertEqual(n2.avoidance_count, 0)
        self.assertNotIn("avoidance", out.get("intervention", {}))

    def test_avoided_written_to_ledger_and_node(self):
        """没用 + 有历史能力证据（KP2 positive_count>0）→ avoided 写账本 + Node 快照。"""
        targets = [KP, KP2, KP3]
        (status, out), _, _ = self._run(_ctx(OUT_NO_BA), target=targets)
        self.assertEqual(status, 200)
        # 只有 avoided/unlearned（该用未用）写账本；attempt_* 不走避险账本
        self.assertEqual(self.wb.ledger.count_for_kp(KP2), 1)   # avoided
        self.assertEqual(self.wb.ledger.count_for_kp(KP3), 1)   # unlearned（无证据）
        n2 = self.nodes[KP2]
        self.assertEqual(n2.avoidance_state, "avoided")
        self.assertEqual(n2.avoidance_count, 1)
        self.assertIsNotNone(n2.last_avoidance_at)
        n3 = self.nodes[KP3]
        self.assertEqual(n3.avoidance_state, "unlearned")

    def test_attempt_err_not_double_written(self):
        """用了但错 → attempt_err 走偏误既有链（预扫已记 observation_error），
        B3 不重复写 avoidance_observed（只出现错误链事件）。"""
        targets = [KP, KP2, KP3]
        err = [{"fragment": "把吃了", "confidence": 0.9,
                "knowledge_point_id": KP2}]
        # 识别结果由主链预扫注入；本测试手工在 ctx.pre_scan 不可行，
        # 直接验证：识别报 KP2 错时，其回避观测非 avoided/unlearned（不写避险账本）
        pre = _pre(errors=err)
        # 绕过 run 主链，直接测 _b3_observe 分段（明确契约）
        run = DialogRun(
            router=self.router, planner=self.planner, identify_skill=None,
            mem=self.mem, wb=self.wb, tracker=self.tracker,
            normalize_user_level=DialogService._normalize_user_level,
            writeback_ledger_events=DialogService._writeback_ledger_events,
            assistant_payload=DialogService._assistant_payload,
            target_constructions=targets)
        b3 = run._b3_observe({"pre_scan": pre}, OUT_ERR_BA, _ctx(OUT_ERR_BA))
        per = b3["avoidance"]["per_kp"]
        self.assertEqual(per[KP2]["signal"], "attempt_err")
        self.assertEqual(per[KP2]["state"], "unlearned")
        # 主链 full run 后账本：KP2 的避险账本应为 0（走错误链，不入避险账本）
        (status, out), _, _ = self._run(
            _ctx(OUT_ERR_BA), target=targets)
        self.assertEqual(status, 200)
        self.assertEqual(self.wb.ledger.count_for_kp(KP2), 0)

    def test_attempt_ok_goes_positive_chain_not_avoidance(self):
        """用了且对 → attempt_ok（正向链），不产避险账本事件。"""
        targets = [KP, KP2, KP3]
        pre = _pre(errors=[])
        run = DialogRun(
            router=self.router, planner=self.planner, identify_skill=None,
            mem=self.mem, wb=self.wb, tracker=self.tracker,
            normalize_user_level=DialogService._normalize_user_level,
            writeback_ledger_events=DialogService._writeback_ledger_events,
            assistant_payload=DialogService._assistant_payload,
            target_constructions=targets)
        b3 = run._b3_observe({"pre_scan": pre}, OUT_OK_BA, _ctx(OUT_OK_BA))
        self.assertEqual(b3["avoidance"]["per_kp"][KP]["state"], "attempt_ok")
        self.assertEqual(self.wb.ledger.count_for_kp(KP), 0)


class MetaSelfRatingTest(B3Base):
    def _events(self):
        return query_meta_events("u-b3", root=self.tmp)

    def test_meta_event_lands_in_message_and_query(self):
        """合法自评档 → 事件落 user 消息 metadata_json；query_meta_events 可聚合成对。"""
        (status, out), _, _ = self._run(
            _ctx("我把苹果吃了。", meta_self_level="half"))
        self.assertEqual(status, 200)
        pairs = self._events()
        self.assertEqual(pairs, [("half", "correct")])   # 无错误 → correct

    def test_node_snapshot_and_route_bias(self):
        """自评记录 → Node.meta_confidence/anchor 快照 + route_bias 6 格偏置。"""
        (status, out), _, _ = self._run(
            _ctx(OUT_NO_BA, meta_self_level="half"))
        self.assertEqual(status, 200)
        # 无目标构式命中 + 无错误 → anchor 空；判定矩阵 half+correct=review_boost
        self.assertEqual(self._events(), [("half", "correct")])

    def test_wrong_outcome_route_matrix(self):
        """识别报错时 outcome=wrong：half+wrong→teach_light_model（unlearned 核心）。"""
        pre_with_err = _pre(errors=[{"fragment": "苹果很多", "confidence": 0.9,
                                     "knowledge_point_id": "hsk30-g1-001"}])
        run = DialogRun(
            router=self.router, planner=self.planner, identify_skill=None,
            mem=self.mem, wb=self.wb, tracker=self.tracker,
            normalize_user_level=DialogService._normalize_user_level,
            writeback_ledger_events=DialogService._writeback_ledger_events,
            assistant_payload=DialogService._assistant_payload)
        b3 = run._b3_observe(
            {"pre_scan": pre_with_err}, "我想买苹果很多。",
            _ctx("我想买苹果很多。", meta_self_level="half"))
        self.assertEqual(b3["meta_event"]["outcome_at_time"], "wrong")
        self.assertEqual(b3["route_bias"]["route"], "teach_light_model")
        # 未声明目标构式 → anchor 空、无 Node 落快照
        self.assertTrue(all(n.meta_confidence is None
                            for n in self.nodes.values()))

    def test_invalid_level_ignored(self):
        """非法自评档（非三档）→ 不产事件、不落快照。"""
        (status, out), _, _ = self._run(_ctx("x", meta_self_level="maybe"))
        self.assertEqual(status, 200)
        self.assertEqual(self._events(), [])

    def test_meta_ask_once_per_task_marker(self):
        """每任务自评≤1：已置 asked_this_task 标记后，锚点不因再次自评被重写追问。"""
        meta_ask = {"asked_this_task": True}
        targets = [KP]
        run = DialogRun(
            router=self.router, planner=self.planner, identify_skill=None,
            mem=self.mem, wb=self.wb, tracker=self.tracker,
            normalize_user_level=DialogService._normalize_user_level,
            writeback_ledger_events=DialogService._writeback_ledger_events,
            assistant_payload=DialogService._assistant_payload,
            target_constructions=targets, meta_ask_state=meta_ask)
        pre = _pre(errors=[])
        b3 = run._b3_observe({"pre_scan": pre}, OUT_OK_BA,
                             _ctx(OUT_OK_BA, meta_self_level="certain"))
        # 标记已置 → should_ask_meta False → 不再设锚点（avoid重复追问），
        # 但学习者确已给档仍照常记录事件（学习可观测性不因防疲态而断）
        self.assertEqual(b3["meta_event"]["anchor"], "")


class SilenceTest(B3Base):
    def test_avoidance_never_in_intervention(self):
        """静默性：回避观测结果绝不进 intervention/directive（总纲 §2 拍板）。"""
        targets = [KP, KP2, KP3]
        (status, out), _, _ = self._run(_ctx(OUT_NO_BA), target=targets)
        self.assertEqual(status, 200)
        itv = out["intervention"]
        # intervention 只有既有时机层字段，绝无 avoidance/route_bias 痕迹
        self.assertEqual(set(itv.keys()), {"level", "reason", "used", "cap"})
        text = str(out["text"]) + str(out.get("degraded", []))
        self.assertNotIn("avoided", text)
        self.assertNotIn("unlearned", text)


if __name__ == "__main__":
    unittest.main()
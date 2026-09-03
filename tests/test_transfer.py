# ============================================================
# 0.22 方向1 · L1 母语迁移归因（迁移带）回归测试
# 覆盖：
#   - transfer：规则表结构；zh/空/ko 不产假设；en 六规则正例+负例；假设形态锁 candidate
#   - recognizer 接线：确认层偏误产 hypotheses、zh 不产、降级路径空
#   - identify_errors 透传：契约输出含 hypotheses[]、图谱零写入（只 ingest errors/uncertain）
#   - explainer D1.4：EN 讲解注入 l1_anchor（确定性、零 LLM）；无命中注入 (none)；ZH 不注入
#   - serve 消费方安全：ledger 编排只读 errors，hypotheses 不产任何账本事件
# 运行: python -m unittest tests.test_transfer -v
# ============================================================

import os
import sys
import tempfile
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.explainer import Explainer
from engine.recognizer import Recognizer
from engine.transfer import load_rules, match, match_one
from skills.identify_errors import IdentifyErrorsSkill


def _err(fragment, correction, etype="语法", kp="", **kw):
    e = {"fragment": fragment, "correction": correction, "type": etype,
         "type_confident": True, "confidence": 0.9,
         "knowledge_point_id": kp, "uncertain": False}
    e.update(kw)
    return e


# ---------------- 规则表与匹配 ----------------

class TransferRulesTest(unittest.TestCase):

    def test_rules_load_six_with_required_keys(self):
        rules = load_rules()
        self.assertEqual(len(rules), 6)
        for r in rules:
            for key in ("rule_id", "kp_anchors", "types", "l1_anchor",
                        "zh_signature", "conf"):
                self.assertIn(key, r, msg=f"{r.get('rule_id')} 缺 {key}")
            self.assertLess(float(r["conf"]), 0.7)  # 永不到确认阈值

    def test_zh_or_empty_native_lang_no_hypotheses(self):
        errs = [_err("三苹果", "三个苹果", kp="kp-liangci")]
        self.assertEqual(match(errs, "zh"), [])
        self.assertEqual(match(errs, ""), [])
        self.assertIsNone(match_one(errs[0], "zh"))

    def test_ko_does_not_borrow_en_rules(self):
        # en 规则只对 en 学习者生效（ko 套 en 规则 = 误诊，宁可不归因）
        errs = [_err("三苹果", "三个苹果", kp="kp-liangci")]
        self.assertEqual(match(errs, "ko"), [])

    def test_language_aliases_normalized(self):
        # "英语"/"English" → en 规则命中；"中文"/"Chinese" → zh 不产假设
        errs = [_err("三苹果", "三个苹果", kp="kp-liangci")]
        self.assertEqual(match(errs, "英语")[0]["rule_id"], "en-classifier-missing")
        self.assertEqual(match(errs, "English")[0]["rule_id"], "en-classifier-missing")
        self.assertEqual(match(errs, "中文"), [])
        self.assertEqual(match(errs, "Chinese"), [])

    def test_classifier_insertion_and_replacement(self):
        hyp = match_one(_err("三苹果", "三个苹果", kp="kp-liangci"), "en")
        self.assertEqual(hyp["rule_id"], "en-classifier-missing")
        hyp2 = match_one(_err("一个手机", "一部手机", kp="kp-liangci"), "en")
        self.assertEqual(hyp2["rule_id"], "en-classifier-missing")

    def test_possessive_de_insertion(self):
        hyp = match_one(_err("我朋友书", "我朋友的书", kp="kp-de-di-de"), "en")
        self.assertEqual(hyp["rule_id"], "en-possessive-de")
        hyp2 = match_one(_err("漂亮衣服", "漂亮的衣服", kp="kp-de-di-de"), "en")
        self.assertEqual(hyp2["rule_id"], "en-possessive-de")

    def test_quantity_reorder(self):
        hyp = match_one(_err("苹果很多", "很多苹果", kp="kp-zhuangyu-chezhi"), "en")
        self.assertEqual(hyp["rule_id"], "en-quantity-adverb-postposed")

    def test_adj_predicate_both_variants(self):
        hyp = match_one(_err("我是高兴", "我很高兴", kp="kp-chengdu-jieci"), "en")
        self.assertEqual(hyp["rule_id"], "en-adj-predicate")
        hyp2 = match_one(_err("她高兴", "她很高兴", kp="kp-chengdu-jieci"), "en")
        self.assertEqual(hyp2["rule_id"], "en-adj-predicate")

    def test_aspect_particle_add_and_remove(self):
        hyp = match_one(_err("我吃", "我吃了", kp="kp-le-dynamic"), "en")
        self.assertEqual(hyp["rule_id"], "en-aspect-particle")
        hyp2 = match_one(_err("他在看书了", "他在看书", kp="kp-zhe"), "en")
        self.assertEqual(hyp2["rule_id"], "en-aspect-particle")

    def test_wh_fronting(self):
        hyp = match_one(_err("什么你要", "你要什么"), "en")
        self.assertEqual(hyp["rule_id"], "en-wh-fronting")

    def test_unrelated_error_no_hypothesis(self):
        # 把字句语序错：无任何规则签名命中 → 不追因（宁漏勿错）
        errs = [_err("把书在桌子上放了", "把书放在桌子上", kp="kp-ba-sentence")]
        self.assertEqual(match(errs, "en"), [])

    def test_negative_signatures(self):
        # 同型但不满足签名的：换序但无数量词 / 插入但非量词 / 疑问词未离句首
        self.assertIsNone(match_one(_err("他学校去", "他去学校", kp="kp-liandong-ju"), "en"))
        self.assertIsNone(match_one(_err("我吃菜", "我喜欢吃菜", kp=""), "en"))
        self.assertIsNone(match_one(_err("什么你要", "什么你要买", kp=""), "en"))

    def test_hypothesis_shape_is_candidate(self):
        hyp = match_one(_err("三苹果", "三个苹果", kp="kp-liangci"), "en")
        self.assertEqual(hyp["status"], "candidate")
        self.assertEqual(hyp["l1"], "en")
        self.assertEqual(hyp["fragment"], "三苹果")
        self.assertEqual(hyp["correction"], "三个苹果")
        self.assertTrue(hyp["l1_anchor"])
        self.assertTrue(hyp["zh_signature"])
        self.assertLess(hyp["conf"], 0.7)

    def test_match_one_error_max_one_per_error(self):
        errs = [_err("三苹果", "三个苹果", kp="kp-liangci"),
                _err("苹果很多", "很多苹果", kp="kp-zhuangyu-chezhi")]
        hyps = match(errs, "en")
        self.assertEqual(len(hyps), 2)  # 每条偏误至多一条


# ---------------- recognizer 接线 ----------------

class _FakeClient:
    def __init__(self, errors=None, raise_exc=None):
        self._errors = errors or []
        self._raise = raise_exc

    def chat_json(self, system, user, temperature=0.0):
        if self._raise:
            raise self._raise
        return {"errors": self._errors}


class RecognizerTransferTest(unittest.TestCase):

    def test_en_confirmed_error_gets_hypotheses(self):
        rec = Recognizer(client=_FakeClient(errors=[
            _err("三苹果", "三个苹果", kp="kp-liangci")]))
        result = rec.recognize("我买了三苹果。", level=2, native_lang="en")
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(len(result["hypotheses"]), 1)
        self.assertEqual(result["hypotheses"][0]["rule_id"],
                         "en-classifier-missing")

    def test_zh_and_empty_no_hypotheses(self):
        for lang in ("zh", ""):
            rec = Recognizer(client=_FakeClient(errors=[
                _err("三苹果", "三个苹果", kp="kp-liangci")]))
            result = rec.recognize("我买了三苹果。", level=2, native_lang=lang)
            self.assertEqual(result["hypotheses"], [], msg=f"native_lang={lang}")

    def test_uncertain_only_no_hypotheses(self):
        # 低置信偏误进 uncertain，不参与归因（归因只对确认层）
        rec = Recognizer(client=_FakeClient(errors=[
            _err("三苹果", "三个苹果", kp="kp-liangci", confidence=0.3)]))
        result = rec.recognize("我买了三苹果。", level=2, native_lang="en")
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["uncertain"]), 1)
        self.assertEqual(result["hypotheses"], [])

    def test_rule_fallback_path_empty_hypotheses(self):
        rec = Recognizer(client=_FakeClient(raise_exc=RuntimeError("llm down")))
        result = rec.recognize("这句话有超纲词测试。", level=1, native_lang="en")
        self.assertIn("degraded", result)
        self.assertEqual(result["hypotheses"], [])


# ---------------- identify_errors 透传 + 图谱零写入 ----------------

class IdentifySkillTransferTest(unittest.TestCase):

    def test_hypotheses_passthrough(self):
        rec = _FakeClient(errors=[_err("三苹果", "三个苹果", kp="kp-liangci")])
        graph = mock.MagicMock()
        skill = IdentifyErrorsSkill(recognizer=Recognizer(client=rec), graph=graph)
        out = skill.run({"text": "我买了三苹果。", "native_lang": "en"})
        self.assertEqual(out["hypotheses"][0]["rule_id"], "en-classifier-missing")

    def test_graph_never_receives_hypotheses(self):
        # _write_graph 只 ingest errors/uncertain：假设键对图谱层不存在
        rec = _FakeClient(errors=[_err("三苹果", "三个苹果", kp="kp-liangci")])
        graph = mock.MagicMock()
        skill = IdentifyErrorsSkill(recognizer=Recognizer(client=rec), graph=graph)
        out = skill.run({"text": "我买了三苹果。", "native_lang": "en"})
        for call in graph.ingest_error.call_args_list:
            payload = call.args[0]
            self.assertNotIn("hypotheses", payload)
            self.assertNotIn("l1_anchor", str(payload))
        self.assertEqual(len(out["hypotheses"]), 1)  # 但契约输出仍在


# ---------------- explainer D1.4 归因注入 ----------------

class ExplainerTransferTest(unittest.TestCase):

    def _capturing_client(self):
        cap = {"system": None, "user": None}

        def chat_json_strict(system, user, temperature=0.4):
            cap["system"], cap["user"] = system, user
            return {"explanation": "ok", "key_points": [], "keywords": [],
                    "uncertain_note": "", "free_generated": False}

        client = mock.MagicMock()
        client.chat_json_strict.side_effect = chat_json_strict
        return client, cap

    def test_en_explain_injects_l1_anchor(self):
        client, cap = self._capturing_client()
        error = {"sentence": "我买了三苹果。", "fragment": "三苹果",
                 "correction": "三个苹果", "type": "语法",
                 "knowledge_point_id": "kp-liangci"}
        Explainer(client=client).explain(error, native_lang="en")
        self.assertIn("en-classifier-missing", cap["system"])
        self.assertIn("no classifier system", cap["system"])
        self.assertIn("native-language habit", cap["system"])

    def test_en_explain_no_match_injects_none(self):
        client, cap = self._capturing_client()
        error = {"sentence": "把书在桌子上放了。", "fragment": "把书在桌子上放了",
                 "correction": "把书放在桌子上", "type": "语法",
                 "knowledge_point_id": "kp-ba-sentence"}
        Explainer(client=client).explain(error, native_lang="en")
        self.assertIn("(none)", cap["system"])

    def test_zh_explain_unaffected(self):
        client, cap = self._capturing_client()
        error = {"sentence": "我买了三苹果。", "fragment": "三苹果",
                 "correction": "三个苹果", "type": "语法",
                 "knowledge_point_id": "kp-liangci"}
        Explainer(client=client).explain(error, native_lang="zh")
        self.assertIn("费曼", cap["system"])
        self.assertNotIn("transfer hypothesis", cap["system"])


# ---------------- serve 消费方安全：ledger 只读 errors ----------------

class LedgerHypothesesSafetyTest(unittest.TestCase):
    """契约消费方（M8 编排）：identify 结果携带 hypotheses[] 时，
    账本只按 errors 的 kp 记 observation_error，假设不产任何事件。"""

    def test_ledger_ignores_hypotheses(self):
        from engine.graph.error_graph import ErrorGraph
        from engine.memory.writeback import Writeback
        from engine.serve import make_handler

        tmp = tempfile.mkdtemp()
        H = make_handler(
            type("R", (), {"learner_id": "x", "graph": ErrorGraph("t")})(), tmp)
        wb = Writeback(graph=ErrorGraph("t2"), root=tmp)
        trace = [{
            "name": "identify_errors", "ok": True,
            "result": {
                "errors": [_err("三苹果", "三个苹果", kp="kp-liangci")],
                "uncertain": [], "degraded": [],
                "hypotheses": [{"rule_id": "en-classifier-missing",
                                "status": "candidate", "fragment": "三苹果",
                                "l1": "en", "conf": 0.5}],
            },
        }]
        H._writeback_ledger_events(H, wb, trace, "我买了三苹果。")
        events = wb.ledger.recent()
        obs = [e for e in events if e.get("kind") == "observation_error"]
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["kp_id"], "kp-liangci")
        # 假设无 kp_id、无事件形态 → 账本事件总数恰为 errors 条数
        self.assertEqual(len(events), 1)


# ---------------- 对话主链：planner 语言注入点亮迁移假设 ----------------

class PlannerIdentifyInjectionTest(unittest.TestCase):
    """0.22 review 修复回归：identify_errors 必须在 LANG_INJECTED_SKILLS——
    否则对话主链（/api/dialog → planner → identify）native_lang 恒空，
    L1 迁移假设在最主要的用户路径上静默失效。"""

    def test_dialog_chain_identify_gets_native_lang_and_hypotheses(self):
        import json as _json
        from engine.graph.error_graph import ErrorGraph
        from planner.loop import LANG_INJECTED_SKILLS, Planner
        from skills import build_registry

        # 静态断言：技能在注入列表里（防未来回归）
        self.assertIn("identify_errors", LANG_INJECTED_SKILLS)

        fake = _FakeClient(errors=[_err("三苹果", "三个苹果", kp="kp-liangci")])
        from engine.recognizer import Recognizer
        reg = build_registry(recognizer=Recognizer(client=fake),
                             graph=ErrorGraph("transfer_plan"))

        def llm(messages):
            if not any(str(m.get("content", "")).startswith("[tool_result]")
                       for m in messages):
                return _json.dumps([{"type": "action", "name": "identify_errors",
                                     "params": {"text": "我买了三苹果。",
                                                "level": "HSK2"}}],
                                   ensure_ascii=False)
            return '[{"type":"text","content":"done"}]'

        r = Planner(reg, llm_call=llm).run("check this", native_lang="en")
        self.assertFalse(r["fallback"])
        t = next(t for t in r["trace"] if t["name"] == "identify_errors")
        # ① 系统注入（LLM 只传了 text/level，native_lang 由 planner 补上）
        self.assertEqual(t["params"].get("native_lang"), "en")
        # ② 主链 tool_result 确实携带迁移假设
        self.assertEqual(t["result"]["hypotheses"][0]["rule_id"],
                         "en-classifier-missing")


if __name__ == "__main__":
    unittest.main()

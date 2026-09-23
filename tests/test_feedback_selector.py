# -*- coding: utf-8 -*-
"""B2 F1/F2/F3 纯函数测试：软偏好表、水平调节、诊断器 bias、深攻选点、缺省容错。

对应实施计划-v3 B2：软偏好不硬锁、深攻按错误点=1、四级优先序选点、
跨回合去重、block 档才深攻、encourage 档全空。纯确定性零 LLM。"""
import unittest

from engine.feedback_selector import (
    select_feedback_strategies, compile_strategy_section,
    _preference, _rank_errors, TYPE_PREFERENCE, LR_TAGS)


def _err(type_, confidence=0.8, kp="kp-x", fragment="错误点",
         verdict=None, diag_v=None, sig="sig"):
    e = {"type": type_, "confidence": confidence,
         "knowledge_point_id": kp, "fragment": fragment}
    if verdict is not None:
        e["verdict"] = verdict
    if diag_v is not None:
        e["construction_diagnostics"] = {"verdict": diag_v}
    return e


class SoftPreferenceTableTest(unittest.TestCase):
    """F1/验收1：软偏好表给出倾向方向（非硬锁）。"""

    def test_types_have_direction(self):
        self.assertEqual(TYPE_PREFERENCE["语用"][0], "input")
        self.assertEqual(TYPE_PREFERENCE["语法"], ("prompt", "metalinguistic"))
        self.assertEqual(TYPE_PREFERENCE["语音"], ("input", "recast"))
        self.assertEqual(TYPE_PREFERENCE["词汇语义"], ("prompt", "clarification_request"))

    def test_unknown_type_falls_back(self):
        d, t, r = _preference(_err("词汇"), 3)
        self.assertEqual(d, "input")
        self.assertIn(t, LR_TAGS)

    def test_lr_tags_valid(self):
        for _, (_, tag) in TYPE_PREFERENCE.items():
            self.assertIn(tag, LR_TAGS)


class LevelAdjustmentTest(unittest.TestCase):
    """F3/规则B：水平调力度（低水平→input 多示范；高水平→prompt push self-repair）。"""

    def test_low_level_prompt_to_input(self):
        d, t, r = _preference(_err("语法"), 2)   # prompt→低水平调 input
        self.assertEqual(d, "input")
        self.assertEqual(t, "explicit_correction")
        self.assertIn("低水平", r)

    def test_high_level_input_to_prompt(self):
        d, t, r = _preference(_err("语音"), 5)   # input→高水平调 prompt
        self.assertEqual(d, "prompt")
        self.assertEqual(t, "elicitation")
        self.assertIn("高水平", r)

    def test_mid_level_keeps_base(self):
        d, t, r = _preference(_err("语法"), 3)
        self.assertEqual((d, t), ("prompt", "metalinguistic"))


class DiagnosticsBiasTest(unittest.TestCase):
    """F3/规则③：语法类+诊断器强信号→显式/元语言（非默认 recast）。"""

    def test_grammar_with_diag_error_metalinguistic(self):
        d, t, r = _preference(_err("语法", diag_v="error"), 3)
        self.assertEqual(t, "metalinguistic")
        self.assertIn("诊断器强信号", r)

    def test_no_diag_error_not_bias(self):
        d, t, r = _preference(_err("语法"), 3)
        self.assertEqual((d, t), ("prompt", "metalinguistic"))
        self.assertEqual(r, "")


class DeepDiveSelectionTest(unittest.TestCase):
    """F2/验收3：深攻选点四级优先序 + 跨回合去重。"""

    def test_diag_signal_first(self):
        diag = _err("语法", confidence=0.5, kp="a", verdict="error", diag_v="error")
        high_conf = _err("语法", confidence=0.95, kp="b")
        ranked = _rank_errors([high_conf, diag])
        self.assertEqual(ranked[0]["knowledge_point_id"], "a")

    def test_recurrence_beats_confidence(self):
        a = _err("语法", confidence=0.9, kp="a")
        b = _err("语法", confidence=0.95, kp="b")
        ranked = _rank_errors([b, a], {"a": 2})
        self.assertEqual(ranked[0]["knowledge_point_id"], "a")

    def test_verdict_severity(self):
        err = _err("语法", kp="a", verdict="error")
        edge = _err("语法", kp="b", verdict="edge")
        self.assertEqual(_rank_errors([edge, err])[0]["knowledge_point_id"], "a")

    def test_stable_lexicographic_tiebreak(self):
        a = _err("语法", confidence=0.9, kp="b", fragment="苹果")
        b = _err("语法", confidence=0.9, kp="a", fragment="香蕉")
        ranked = _rank_errors([b, a])
        self.assertEqual([e["fragment"] for e in ranked], ["苹果", "香蕉"])

    def test_block_only_deep_dive(self):
        errs = [_err("语法", kp="a"), _err("语法", kp="b")]
        s = select_feedback_strategies(errs, 3, "block")
        self.assertIsNotNone(s["deep_dive"])
        self.assertEqual(s["deep_dive_count"], 1)

    def test_none_level_light_only(self):
        errs = [_err("语法", kp="a"), _err("语法", kp="b"),
                _err("语法", kp="c"), _err("语法", kp="d")]
        s = select_feedback_strategies(errs, 3, "none")
        self.assertIsNone(s["deep_dive"])
        self.assertEqual(s["deep_dive_count"], 0)
        self.assertLessEqual(len(s["light_marks"]), 2)

    def test_encourage_all_empty(self):
        s = select_feedback_strategies([_err("语法", kp="a")], 3, "encourage")
        self.assertIsNone(s["deep_dive"])
        self.assertEqual(s["light_marks"], [])
        self.assertEqual(len(s["preferences"]), 0)

    def test_cross_turn_dedup(self):
        errs = [_err("语法", kp="a"), _err("语法", kp="b")]
        s2 = select_feedback_strategies(errs, 3, "block", deep_dived_kps={"a"})
        self.assertEqual(s2["deep_dive"]["knowledge_point_id"], "b")


class RobustnessTest(unittest.TestCase):
    """验收补充断言：B0/B1 字段缺省容错 + 去重同 kp。"""

    def test_missing_verdict_diagnostics_ok(self):
        errs = [_err("语法", kp="a"), _err("语法", kp="b")]
        s = select_feedback_strategies(errs, 3, "light")
        self.assertEqual(len(s["preferences"]), len(s["light_marks"]))
        self.assertIsNone(s["deep_dive"])

    def test_deep_dive_same_kp_dedup_across_silent(self):
        errs = [_err("语法", kp="a", fragment="x"), _err("语法", kp="a", fragment="y")]
        s = select_feedback_strategies(errs, 3, "block", deep_dived_kps={"a"})
        self.assertIsNone(s["deep_dive"])   # 唯一候选 kp 已深攻 → 本回合深攻空

    def test_all_ranked_counted(self):
        errs = [_err(t, kp=f"k{i}", confidence=0.7 + i * 0.05)
                for i, t in enumerate(["语法", "语用", "语音", "词汇语义"])]
        s = select_feedback_strategies(errs, 3, "block")
        n_deep = s["deep_dive_count"]
        total = n_deep + len(s["light_marks"]) + len(s["silent_kps"])
        self.assertEqual(total, len(errs))


class CompileSectionTest(unittest.TestCase):
    """F4：策略段编译双语 + silent 不进段。"""

    def test_zh_section_has_strategy_marks(self):
        s = select_feedback_strategies(
            [_err("语法", kp="a", fragment="把这件事知道"),
             _err("语用", kp="b", fragment="你几岁")], 3, "block")
        sec = compile_strategy_section(s, "zh")
        self.assertIn("[FeedbackStrategy]", sec)
        self.assertIn("把这件事知道", sec)
        self.assertIn("你几岁", sec)
        self.assertIn("复习队列", sec)

    def test_en_section(self):
        s = select_feedback_strategies([_err("语法", kp="a")], 3, "block")
        sec = compile_strategy_section(s, "en")
        self.assertIn("[FeedbackStrategy]", sec)

    def test_empty_when_nothing(self):
        s = select_feedback_strategies([], 3, "block")
        self.assertEqual(compile_strategy_section(s, "zh"), "")


if __name__ == "__main__":
    unittest.main()
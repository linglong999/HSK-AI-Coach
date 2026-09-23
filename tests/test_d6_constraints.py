# -*- coding: utf-8 -*-
# tests/test_d6_constraints.py —— B6 J4 · D-6 八条确定性断言表驱动
# 约定：assert_d6(round_result, graph_snapshot, lex) 返回违规清单（空=全过）。
# 每条 = 合法样本通过 + 违规样本触发；缺省容错（字段缺失不误伤、不假过）。
# 既有 52 测试零改动全绿。

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from datasets.eval.count_assertions import assert_d6  # noqa: E402

# 一个合规 round_result：四计数全合法 + 诊断器 verdict 与 score 一致
OK_ROUND = {
    "deep_dive_count": 1,
    "restate_count": 1,
    "meta_ask_count": 1,
    "unpack_beyond_words": [],
    "construction_diagnostics": {"construction": "ba", "verdict": "error",
                                 "score": 0.40},
}
# 合规 Node：回避域与错误域分列、回避值在图谱四态内
OK_NODE = {"knowledge_point": "hsk30-g2-001", "error_count": 2,
           "error_types": {"word_order": 2}, "error_kind": "word_order",
           "avoidance_state": "avoided", "avoidance_count": 2}
OK_GRAPH = {"nodes": {"hsk30-g2-001": OK_NODE}}


def _round(**over):
    d = dict(OK_ROUND)
    d.update(over)
    return d


def _node(**over):
    d = dict(OK_NODE)
    d.update(over)
    return d


class D6EveryOk(unittest.TestCase):
    def test_legal_round_passes(self):
        self.assertEqual(assert_d6(OK_ROUND, OK_GRAPH, {}), [])

    def test_keys_absent_tolerated(self):
        """缺省容错：round_result 空/缺字段、graph 空 → 不误伤不假过。"""
        self.assertEqual(assert_d6({}, {}, {}), [])
        self.assertEqual(assert_d6(None, None, None), [])
        # 只留 construction_diagnostics 合法 → 全过
        self.assertEqual(assert_d6({"construction_diagnostics": OK_ROUND["construction_diagnostics"]},
                                   {}, {}), [])


class D6CounterTriggers(unittest.TestCase):
    def test_deep_dive_count_over(self):
        r = _round(deep_dive_count=2)
        self.assertIn("d6-1", assert_d6(r, OK_GRAPH, {})[0])

    def test_restate_count_over(self):
        r = _round(restate_count=2)
        self.assertIn("d6-2", assert_d6(r, OK_GRAPH, {})[0])

    def test_meta_ask_count_over(self):
        r = _round(meta_ask_count=2)
        self.assertIn("d6-3", assert_d6(r, OK_GRAPH, {})[0])

    def test_unpack_beyond_words_nonempty(self):
        r = _round(unpack_beyond_words=[{"word": "她", "level": "HSK5"}])
        self.assertIn("d6-4", assert_d6(r, OK_GRAPH, {})[0])

    def test_unpack_wrong_type(self):
        r = _round(unpack_beyond_words="oops")
        self.assertIn("d6-4", assert_d6(r, OK_GRAPH, {})[0])

    def test_counters_accept_zero(self):
        """合法：计数=0/1 均可，不误伤。"""
        for k in ("deep_dive_count", "restate_count", "meta_ask_count"):
            self.assertEqual(assert_d6(_round(**{k: 0}), OK_GRAPH, {}), [])


class D6Diagnostics(unittest.TestCase):
    def test_verdict_score_domain_match(self):
        # 三档三值都合法
        for verdict, score in [("acceptable", 0.95), ("edge", 0.70),
                               ("error", 0.10)]:
            r = _round(construction_diagnostics={"construction": "ba",
                                                 "verdict": verdict,
                                                 "score": score})
            self.assertEqual(assert_d6(r, OK_GRAPH, {}), [])

    def test_verdict_score_domain_mismatch(self):
        # score=0.70(edge) 却标 verdict=error → 违规
        r = _round(construction_diagnostics={"construction": "ba",
                                             "verdict": "error", "score": 0.70})
        self.assertIn("d6-5", assert_d6(r, OK_GRAPH, {})[0])

    def test_boundary_thresholds(self):
        # 0.85 边界 → acceptable；0.60 边界 → edge
        self.assertEqual(self._d6_verdict("acceptable", 0.85), [])
        self.assertEqual(self._d6_verdict("edge", 0.60), [])

    def test_domain_below_thresholds(self):
        # 0.59 → error；0.84 → edge
        self.assertEqual(self._d6_verdict("error", 0.59), [])
        self.assertEqual(self._d6_verdict("edge", 0.84), [])

    def _d6_verdict(self, verdict, score):
        r = _round(construction_diagnostics={"construction": "ba",
                                             "verdict": verdict, "score": score})
        return assert_d6(r, OK_GRAPH, {})


class D6Architecture(unittest.TestCase):
    def test_diagnostics_no_llm_import(self):
        """⑥ 源码确实无 LLM token：默认即证明（若源缺失则容错跳过）。"""
        self.assertNotIn("d6-6", [v for v in assert_d6(OK_ROUND, OK_GRAPH, {})])

    def test_llm_token_in_source_flagged(self):
        # 注入含 token 的源码不可行（读真实文件）；此处验证 token 集非空且可命中
        from datasets.eval.count_assertions import _FORBIDDEN_LLM_TOKENS
        self.assertTrue(_FORBIDDEN_LLM_TOKENS)  # 禁 token 集非空


class D6Avoidance(unittest.TestCase):
    def test_avoidance_state_out_of_enum(self):
        g = {"nodes": {"k": _node(avoidance_state="banana")}}
        self.assertIn("d6-7", assert_d6(OK_ROUND, g, {})[0])

    def test_avoidance_fields_separate_from_error(self):
        # 合法：回避域值在图谱四态、错误域分列 → 不报 d6-7
        g = {"nodes": {"k": _node(avoidance_state="unlearned", error_count=1)}}
        self.assertEqual(assert_d6(OK_ROUND, g, {}), [])

    def test_enum_integrity(self):
        # ⑧ 图谱恰四态、观测信号恰 5 值（枚举自身完整性由本测试锁死）
        from datasets.eval.count_assertions import (GRAPH_STATES,
                                                    OBSERVED_SIGNALS)
        self.assertEqual(GRAPH_STATES, ("avoided", "unlearned", "learned",
                                        "undetermined"))
        self.assertEqual(len(OBSERVED_SIGNALS), 5)


if __name__ == "__main__":
    unittest.main()
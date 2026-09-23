# -*- coding: utf-8 -*-
"""B3 G3 回避观测纯函数测试（零 LLM）：四态表驱动 +
attempt_ok 走正向链不产 unused 事件 + 两组枚举映射不混用断言。"""
import unittest

from engine.avoidance_observer import (
    GRAPH_STATES, OBSERVED_SIGNALS, observe_avoidance, _contains_construction_marker)


class _Node:
    def __init__(self, positive_count=0):
        self.positive_count = positive_count


class ObserveAvoidanceTest(unittest.TestCase):
    """验收3：回避判据对齐——用得多错率高=未习得 vs 用得少绕行=回避。"""

    def _run(self, targets, output="", errors=None, nodes=None):
        res = {"errors": errors or [], "uncertain": []}
        return observe_avoidance(targets, output, res, nodes or {})

    def test_inactive_without_targets(self):
        r = self._run([], output="我把这件事知道")
        self.assertEqual(r["per_kp"], {})
        self.assertFalse(r["trace"]["active"])

    def test_attempt_err_when_reported(self):
        r = self._run(["kp-ba"], output="我把这件事知道",
                      errors=[{"knowledge_point_id": "kp-ba"}],
                      nodes={"kp-ba": _Node(2)})
        p = r["per_kp"]["kp-ba"]
        self.assertEqual(p["signal"], "attempt_err")
        self.assertEqual(p["state"], "unlearned")
        self.assertIn("kp-ba", r["trace"]["err_kps"])

    def test_attempt_ok_positive_chain(self):
        r = self._run(["kp-ba"], output="我把书放在桌子上了")
        p = r["per_kp"]["kp-ba"]
        self.assertEqual(p["signal"], "attempt_ok")
        self.assertEqual(p["state"], "attempt_ok")   # 走正向链，本模块不产 unlearned

    def test_avoided_with_history(self):
        # 用得少 + 有绕行/没用 + 有历史能力 → 回避（该复习路径）
        r = self._run(["kp-ba"], output="我在桌子上放了书",
                      nodes={"kp-ba": _Node(3)})
        p = r["per_kp"]["kp-ba"]
        self.assertEqual(p["state"], "avoided")
        self.assertEqual(p["signal"], "avoided")

    def test_unlearned_without_history(self):
        # 没用 + 无能力证据 → unlearned（该教路径）
        r = self._run(["kp-ba"], output="我在桌子上放了书",
                      nodes={"kp-ba": _Node(0)})
        p = r["per_kp"]["kp-ba"]
        self.assertEqual(p["state"], "unlearned")

    def test_avoid_vs_unlearned_distinct_paths(self):
        # 验收3核心：有用错=unlearned、没用有史=avoided，两路分立
        err = self._run(["kp-ba"], output="我把这件事知道",
                        errors=[{"knowledge_point_id": "kp-ba"}])
        avd = self._run(["kp-ba"], output="我在桌子上放了书",
                        nodes={"kp-ba": _Node(5)})
        self.assertTrue(all(v["state"] == "unlearned"
                            for v in err["per_kp"].values()))
        self.assertTrue(all(v["state"] == "avoided"
                            for v in avd["per_kp"].values()))

    def test_enum_sets_do_not_mix(self):
        # 验收补充：观测信号 ≠ 图谱四态，职责不同（观测多 attempt_ok/err；
        # 图谱多 learned；undetermined/avoided/unlearned 为共名，观测侧是瞬时判、
        # 图谱侧是归档态）。映射靠本函数显式产出不靠枚举混用。
        self.assertNotIn("learned", OBSERVED_SIGNALS)
        self.assertNotIn("attempt_ok", GRAPH_STATES)
        self.assertNotIn("attempt_err", GRAPH_STATES)
        self.assertIn("learned", GRAPH_STATES)
        self.assertIn("attempt_ok", OBSERVED_SIGNALS)

    def test_attempt_ok_leaves_replacement_observation(self):
        r = self._run(["kp-ba"], output="我把它放在桌子上")
        self.assertEqual(r["per_kp"]["kp-ba"]["replacement"], "")

    def test_trace_carries_learner_output(self):
        r = self._run(["kp-ba"], output="我放了书在桌子上呀")
        self.assertEqual(r["trace"]["learner_output"], "我放了书在桌子上呀")

    def test_multiple_targets(self):
        r = self._run(["kp-ba", "kp-bu"], output="没放好",
                      errors=[{"knowledge_point_id": "kp-ba"}],
                      nodes={"kp-ba": _Node(0), "kp-bu": _Node(4)})
        self.assertEqual(r["per_kp"]["kp-ba"]["signal"], "attempt_err")
        self.assertEqual(r["per_kp"]["kp-bu"]["state"], "avoided")


class MarkerTest(unittest.TestCase):
    def test_empty_output(self):
        self.assertFalse(_contains_construction_marker(""))
        self.assertFalse(_contains_construction_marker(None))

    def test_ba_marker(self):
        self.assertTrue(_contains_construction_marker("我把书放了"))

    def test_no_marker(self):
        self.assertFalse(_contains_construction_marker("早上好"))


if __name__ == "__main__":
    unittest.main()
# -*- coding: utf-8 -*-
"""B3 G1 元认知判定纯函数测试（零 LLM）：6 格矩阵表驱动全过 +
should_ask_meta 两高危时机/已问拦截/防疲态 + 自评档枚举校验。"""
import unittest

from engine.meta_judgment import (
    SELF_LEVELS, MATRIX, route_bias, should_ask_meta)


class RouteBiasMatrixTest(unittest.TestCase):
    """验收1：判定矩阵 6 格路由偏置正确（含 half+wrong=unlearned 核心）。"""

    def test_six_cells_present(self):
        self.assertEqual(len(MATRIX), 6)
        for k in SELF_LEVELS:
            self.assertIn((k, "correct"), MATRIX)
            self.assertIn((k, "wrong"), MATRIX)

    def test_certain_correct_advance(self):
        b = route_bias("certain", "correct")
        self.assertEqual(b["route"], "advance")
        self.assertEqual(b["scheduling"], "long_interval")

    def test_certain_wrong_light_mark(self):
        b = route_bias("certain", "wrong")
        self.assertEqual(b["route"], "light_mark")
        self.assertIn("能力错觉", b["note"])

    def test_half_correct_review_boost(self):
        b = route_bias("half", "correct")
        self.assertEqual(b["route"], "review_boost")

    def test_half_wrong_unlearned_core(self):
        # B3-A 拍板核心：半确定+产出错 = unlearned 核心信号
        b = route_bias("half", "wrong")
        self.assertEqual(b["route"], "teach_light_model")
        self.assertIn("unlearned", b["note"])

    def test_uncertain_correct_encourage(self):
        b = route_bias("uncertain", "correct")
        self.assertEqual(b["route"], "encourage")

    def test_uncertain_wrong_silent(self):
        b = route_bias("uncertain", "wrong")
        self.assertEqual(b["route"], "silent_or_model")

    def test_invalid_level_empty(self):
        b = route_bias("sure", "correct")
        self.assertEqual(b["route"], "")

    def test_route_never_decides_correctness(self):
        # 偏置结构只承载路由/调度，不含 verdict（对错由 B1 主判）
        for (lv, oc), v in MATRIX.items():
            self.assertNotIn("verdict", v)
            self.assertNotIn("score", v)

    def test_half_documented_as_calibration_gap(self):
        # B3-A：half 是校准 gap 中间态，非第三习得状态
        # —— 语义证据：矩阵中 half 两格路由均为"强化教学"类，无任何"已习得"旁证
        self.assertNotIn("advance", route_bias("half", "correct")["route"])
        self.assertEqual(route_bias("half", "correct")["route"], "review_boost")
        self.assertEqual(route_bias("half", "wrong")["route"], "teach_light_model")


class ShouldAskMetaTest(unittest.TestCase):
    """验收：自评时机两高危 + 每任务≤1 + 防疲态。"""

    def test_hit_target_correct_asks(self):
        ask, anchor = should_ask_meta(["kp-ba-sentence"], False, False)
        self.assertTrue(ask)
        self.assertEqual(anchor, "kp-ba-sentence")

    def test_hit_target_with_error_asks(self):
        ask, anchor = should_ask_meta(["kp-ba"], True, False)
        self.assertTrue(ask)
        self.assertEqual(anchor, "kp-ba")

    def test_error_without_target_still_asks_unnchored(self):
        ask, anchor = should_ask_meta([], True, False)
        self.assertTrue(ask)
        self.assertEqual(anchor, "")

    def test_no_target_no_error_no_ask(self):
        ask, anchor = should_ask_meta([], False, False)
        self.assertFalse(ask)
        self.assertEqual(anchor, "")

    def test_already_asked_blocks(self):
        ask, _ = should_ask_meta(["kp-ba"], True, True)
        self.assertFalse(ask)          # 防疲态：已问 → 一律 False（每任务≤1）
        ask2, _ = should_ask_meta(["kp-ba"], False, True)
        self.assertFalse(ask2)

    def test_anchor_is_standard_id(self):
        # 锚点必须标准 kp_id，不落自由文本（G2 校验器消费）
        _, anchor = should_ask_meta(["kp-liangci 专属量词"], False, False)
        self.assertIn("kp-liangci", anchor)


if __name__ == "__main__":
    unittest.main()
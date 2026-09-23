# -*- coding: utf-8 -*-
"""B5 I1 · 脚手架渐褪（engine/scaffold.py 纯确定性）

覆盖：
  - pick_tier 三段映射（1/2→full、3/4→mid、5+→minimal；越界/非数→minimal 兜底）
  - pick_stage 表驱动（mastery×verify 合成：低 mastery/近期 fail→回撤一档，
    高 mastery+全 pass→撤一档；其余→基准档）
  - scaffold_directive 英文域在所有档最先撤（full>mid>minimal 英文指令递减）、
    中文恒锚断言
  - 选档全程零 LLM（模块无 LLM 依赖——import 面无 client/chat）
运行: python -m unittest tests.test_scaffold -v
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.scaffold import (  # noqa: E402
    PERF_LADDER,
    pick_stage,
    pick_tier,
    scaffold_directive,
)


class PickTierTest(unittest.TestCase):
    """第一层：等级 → 初始档"""

    def test_level_1_2_full(self):
        self.assertEqual(pick_tier(1), "full")
        self.assertEqual(pick_tier(2), "full")

    def test_level_3_4_mid(self):
        self.assertEqual(pick_tier(3), "mid")
        self.assertEqual(pick_tier(4), "mid")

    def test_level_5_9_minimal(self):
        self.assertEqual(pick_tier(5), "minimal")
        self.assertEqual(pick_tier(6), "minimal")
        self.assertEqual(pick_tier(9), "minimal")

    def test_out_of_range_fallback_minimal(self):
        # 非正/超上限 → minimal 兜底（不抛、不裸判）
        self.assertEqual(pick_tier(0), "minimal")
        self.assertEqual(pick_tier(10), "minimal")
        self.assertEqual(pick_tier(-1), "minimal")

    def test_non_number_fallback_minimal(self):
        self.assertEqual(pick_tier(None), "minimal")
        self.assertEqual(pick_tier("x"), "minimal")


class PickStageTest(unittest.TestCase):
    """第二层：表现 → 熟练度分 → 档位（表驱动）"""

    def test_empty_verify_neutral(self):
        # 无近期表现 → 基准档（不异常回撤）
        self.assertEqual(pick_stage(1, 0.5), PERF_LADDER[0])       # full 基准 0
        self.assertEqual(pick_stage(3, 0.5), PERF_LADDER[1])       # mid 基准 1
        self.assertEqual(pick_stage(5, 0.5), PERF_LADDER[2])       # minimal 基准 2
        self.assertEqual(pick_stage(1, 0.5, recent_verify=[]), PERF_LADDER[0])

    def test_low_mastery_rollback(self):
        # 低 mastery → 回撤一档（多给脚手架）
        self.assertEqual(pick_stage(3, 0.1), PERF_LADDER[0])       # mid 基准1 →0
        self.assertEqual(pick_stage(5, 0.0), PERF_LADDER[1])       # minimal 基准2 →1

    def test_fail_rollback(self):
        # 近期 fail → 回撤一档
        self.assertEqual(pick_stage(1, 0.5, ["fail"]), PERF_LADDER[0])  # full 基准0已最全
        self.assertEqual(pick_stage(3, 0.5, ["fail"]), PERF_LADDER[0])  # mid 基准1 →0
        self.assertEqual(pick_stage(5, 0.5, ["fail"]), PERF_LADDER[1])  # minimal 基准2 →1

    def test_partial_rollback(self):
        self.assertEqual(pick_stage(3, 0.5, ["partial"]), PERF_LADDER[0])

    def test_high_mastery_pass_strip(self):
        # 高 mastery + 全 pass → 撤一档（脚手架更少）
        self.assertEqual(pick_stage(1, 0.8, ["pass"]), PERF_LADDER[1])  # full 0 →1
        self.assertEqual(pick_stage(3, 0.9, ["pass"]), PERF_LADDER[2])  # mid 1 →2
        self.assertEqual(pick_stage(5, 0.9, ["pass"]), PERF_LADDER[3])  # minimal 2 →3

    def test_high_mastery_no_pass_keeps_baseline(self):
        # 高 mastery 但近期无 pass → 不额外撤档
        self.assertEqual(pick_stage(3, 0.9, ["partial"]), PERF_LADDER[0])
        self.assertEqual(pick_stage(3, 0.9, []), PERF_LADDER[1])

    def test_verify_dict_entries(self):
        # verify 元素可为 dict（含 verdict）——兼容编排层产物
        self.assertEqual(pick_stage(3, 0.5, [{"verdict": "fail"}]), PERF_LADDER[0])
        self.assertEqual(pick_stage(3, 0.5, [{"verdict": "partial"}]), PERF_LADDER[0])

    def test_ladder_cap_no_overflow(self):
        # 高 mastery+pass 已在档顶 → 不越界
        self.assertEqual(pick_stage(5, 0.9, ["pass"]), PERF_LADDER[3])  # 顶


class ScaffoldDirectiveTest(unittest.TestCase):
    """档位 → prompt 注入段"""

    def test_english_removed_first_across_tiers(self):
        # 英文指令随量的加深而递减（既然多给了中文解包，英文翻译不再全盘给）
        # 语义断言：full 档明示给英文翻译；minimal 档明示撤英文
        full_d = scaffold_directive("full", PERF_LADDER[0])
        minimal_d = scaffold_directive("minimal", PERF_LADDER[3])
        self.assertIn("英文翻译", full_d)
        self.assertIn("撤英文", minimal_d)

    def test_chinese_anchor_always_present(self):
        # 中文解包恒锚：任意档都含"以学习者等级内已掌握词展开"
        for t in ("full", "mid", "minimal"):
            for s in PERF_LADDER:
                d = scaffold_directive(t, s)
                self.assertIn("中文", d)
                self.assertIn("恒锚", d)

    def test_bilingual_template(self):
        d = scaffold_directive("full", PERF_LADDER[0], bilingual=True)
        self.assertIn("Full scaffold", d)
        self.assertIn("Chinese", d)

    def test_unknown_stage_falls_back_full(self):
        # 未知档位 → 全脚手架兜底（不抛）
        d = scaffold_directive("full", "bogus")
        self.assertIn("英文翻译", d)

    def test_no_llm_in_module_source(self):
        # 选档全程零 LLM：import 面无 client/chat/openai 等词（scaffold 应纯确定性）
        src = open(os.path.join(_PROJECT_ROOT, "src", "engine", "scaffold.py"),
                   encoding="utf-8").read()
        for banned in ("LLMClient", "chat_json", "openai", ".client(", "requests"):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main()
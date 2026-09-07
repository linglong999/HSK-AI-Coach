# ============================================================
# 生成单元 authoring 契约 + 校验器测试（tests/test_authoring.py）
# Archify P1（authoring/schema 互指）+ P2（稳定 code）+ P3（修复定序）
# ============================================================

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.generation.authoring import (
    build_explain_prompt,
    build_practice_prompt,
    EXPLAIN_AUTHORING_PROMPT,
    PRACTICE_AUTHORING_PROMPT,
    UNIT_BASE_FIELDS,
    EXPLAIN_FIELDS,
    PRACTICE_FIELDS,
    POSTPONED_FIELDS,
    SCENE_TYPES,
    PRACTICE_TASK_KINDS,
)
from engine.generation.validator import GenerationUnitValidator


class TestAuthoringPrompt(unittest.TestCase):
    """authoring prompt：槽位填充正确，关键字段口径进入 prompt。"""

    def test_explain_prompt_fills_slots(self):
        p = build_explain_prompt(for_keypoint="量词'只'",
                                 teaching_objective="讲清量词用法",
                                 curriculum_at="HSK1-量词",
                                 all_titles=["课文一"],
                                 self_language="zh")
        self.assertIn("量词'只'", p)
        self.assertIn("讲清量词用法", p)
        self.assertIn("HSK1-量词", p)
        self.assertIn('"type": "explain"', p)
        self.assertIn("allTitles", p)
        self.assertIn("keyPoints", p)
        self.assertIn("forbidden_errors", p)
        self.assertIn("dialogueSpec", p)

    def test_explain_prompt_injects_common_rules(self):
        p = build_explain_prompt()
        # 公共护栏：keyPoints 只装正向知识
        self.assertIn("正向目标知识", p)
        # 后置字段一律 null
        self.assertIn("一律写 null", p)
        # 不自由造 type
        self.assertIn("枚举值", p)

    def test_practice_prompt_enum_guard(self):
        p = build_practice_prompt(task_kind="bogus")
        # 非法 task_kind 应回落 mcq
        self.assertIn('"task_kind": "fill|mcq|rephrase|correct|open-ended 之一', p)

    def test_practice_prompt_difficulty_note(self):
        p = build_practice_prompt()
        self.assertIn("difficulty", p)
        self.assertIn("服务层", p)  # 不信任生成侧自报难度

    def test_example_matches_schema_v1(self):
        # 口径冒烟：两个模板都声明 UnitBase 关键字段 + 后置位
        for tpl in (EXPLAIN_AUTHORING_PROMPT, PRACTICE_AUTHORING_PROMPT):
            for f in ("id", "type", "title", "keyPoints", "forbidden_errors",
                      "context", "dialogueSpec", "interactiveSpec", "pblSpec"):
                self.assertIn(f, tpl)


class TestValidator(unittest.TestCase):
    """生成单元校验器：稳定 code + 修复定序，无 LLM 依赖。"""

    def setUp(self):
        self.v = GenerationUnitValidator()

    def test_valid_explain_passes(self):
        unit = {
            "id": "u-1", "type": "explain", "title": "量词",
            "keyPoints": ["量词'只'用于个体"], "forbidden_errors": [],
            "context": {"allTitles": ["课文一"], "curriculumAt": "HSK1-量词"},
            "for_keypoint": "量词'只'", "teachingObjective": "讲清量词",
            "estimatedDuration": 90,
        }
        diags = self.v.validate(unit)
        self.assertEqual([d.code for d in diags], [])

    def test_invalid_json(self):
        diags = self.v.validate("{not json")
        self.assertEqual(diags[0].code, "code_invalid_json")
        self.assertEqual(diags[0].repair_order(), 0)

    def test_unknown_type(self):
        diags = self.v.validate({"id": "x", "type": "freeform"})
        self.assertEqual(diags[0].code, "code_unknown_type")
        self.assertEqual(diags[0].evidence, "freeform")

    def test_missing_required(self):
        unit = {"id": "x", "type": "explain", "title": "t"}
        diags = self.v.validate(unit)
        codes = [d.code for d in diags]
        self.assertIn("code_missing_required", codes)
        # 必含 keyPoints 空数组诊断
        self.assertIn("code_keypoints_empty", codes)

    def test_forbidden_missing_from_none(self):
        unit = {"id": "x", "type": "practice", "title": "t",
                "forbidden_errors": None, "keyPoints": ["k"],
                "task_kind": "mcq", "for_keypoints": ["a"], "questionCount": 3,
                "estimatedDuration": 150}
        diags = self.v.validate(unit)
        codes = [d.code for d in diags]
        self.assertIn("code_forbidden_missing", codes)

    def test_postponed_not_null(self):
        unit = {"id": "x", "type": "explain", "title": "t",
                "keyPoints": ["k"], "forbidden_errors": [],
                "context": {"curriculumAt": "p"}, "dialogueSpec": {"scene": "x"},
                "teachingObjective": "o", "estimatedDuration": 1}
        diags = self.v.validate(unit)
        self.assertIn("code_postponed_not_null", [d.code for d in diags])

    def test_task_kind_invalid(self):
        unit = {"id": "x", "type": "practice", "title": "t",
                "keyPoints": ["k"], "forbidden_errors": [],
                "context": {"curriculumAt": "p"}, "for_keypoints": ["a"],
                "task_kind": "essay", "questionCount": 3,
                "estimatedDuration": 150}
        diags = self.v.validate(unit)
        d = next(x for x in diags if x.code == "code_task_kind_invalid")
        self.assertEqual(d.evidence, "essay")

    def test_question_count_cap(self):
        unit = {"id": "x", "type": "practice", "title": "t",
                "keyPoints": ["k"], "forbidden_errors": [],
                "context": {"curriculumAt": "p"}, "for_keypoints": ["a"],
                "task_kind": "mcq", "questionCount": 99,
                "estimatedDuration": 150}
        diags = self.v.validate(unit)
        self.assertIn("code_question_count_high", [d.code for d in diags])

    def test_p2_evidence_shape(self):
        # 符合 Archify P2：诊断含 code/evidence/supported_fixes
        unit = {"type": "freeform"}
        d = self.v.validate(unit)[0].to_dict()
        self.assertIn("code", d)
        self.assertIn("evidence", d)
        self.assertIn("supported_fixes", d)
        self.assertIn("repair_order", d)

    def test_p3_repair_order_ordering(self):
        # 正确性(0) 应先于 语义(2/3) 先于 结构(5)；list 按 order 升序
        unit = {"id": "x", "type": "practice", "title": "t",
                "keyPoints": [], "forbidden_errors": [],
                "context": {"curriculumAt": "p"}, "for_keypoints": ["a"],
                "task_kind": "bogus", "questionCount": 99,
                "estimatedDuration": 150}
        diags = self.v.validate(unit)
        orders = [d.repair_order() for d in diags]
        self.assertEqual(orders, sorted(orders), f"应按修复定序升序: {orders}")


class TestContractConsistency(unittest.TestCase):
    """authoring / schema / 校验器三方口径一致（Archify P1：contract 与 schema 互指）。"""

    def test_schema_constants_complete(self):
        # 首发的两个单元类型，所需字段清单非空且互不串位
        self.assertIn("explain", SCENE_TYPES)
        self.assertIn("practice", SCENE_TYPES)
        self.assertLessEqual(set(PRACTICE_TASK_KINDS),
                             {"fill", "mcq", "rephrase", "correct", "open-ended"})
        self.assertEqual(set(POSTPONED_FIELDS),
                         {"dialogueSpec", "interactiveSpec", "pblSpec"})

    def test_explain_fields_contain_base(self):
        self.assertTrue(set(UNIT_BASE_FIELDS).issubset(set(EXPLAIN_FIELDS)))
        self.assertIn("teachingObjective", EXPLAIN_FIELDS)
        self.assertIn("estimatedDuration", EXPLAIN_FIELDS)

    def test_practice_fields_contain_base(self):
        self.assertTrue(set(UNIT_BASE_FIELDS).issubset(set(PRACTICE_FIELDS)))
        self.assertIn("task_kind", PRACTICE_FIELDS)
        self.assertIn("questionCount", PRACTICE_FIELDS)
        self.assertIn("targets_errors", PRACTICE_FIELDS)
        self.assertIn("difficulty", PRACTICE_FIELDS)
        self.assertIn("estimatedDuration", PRACTICE_FIELDS)


if __name__ == "__main__":
    unittest.main()
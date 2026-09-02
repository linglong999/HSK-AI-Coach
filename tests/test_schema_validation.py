# ============================================================
# 0.18 接入项④a · 技能 schema 校验 测试
# 覆盖：
#   - validate_params 单测：required / 安全纠正 / enum / 宽松放行 / bool-int 陷阱
#   - 9 技能 schema 完备性快照（input/output 均非空）
#   - planner 集成：校验失败 → 技能不执行 → 错误喂回 → 下一轮自纠成功
# 运行: python -m unittest tests.test_schema_validation -v
# ============================================================

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills import build_registry
from skills.base import Skill
from planner.loop import Planner, is_tool_result_message


class _EchoSkill(Skill):
    """测试用探针技能：记录收到的 params，回显确认。"""
    metadata = {"name": "echo_probe", "version": "0.1", "summary": "测试探针"}
    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
            "repeats": {"type": "integer"},
            "kp_only": {"type": "boolean"},
        },
    }

    def __init__(self):
        super().__init__()
        self.calls = []

    def run(self, context):
        self.calls.append(context)
        return {"echo": context.get("text"), "repeats": context.get("repeats")}


class ValidatorUnitTest(unittest.TestCase):
    def setUp(self):
        self.skill = _EchoSkill()

    def test_missing_required(self):
        _, errors = self.skill.validate_params({"repeats": 2})
        self.assertEqual(len(errors), 1)
        self.assertIn("text", errors[0])
        self.assertIn("echo_probe", errors[0])   # 错误消息带技能名供 LLM 定位

    def test_blank_required_treated_as_missing(self):
        _, errors = self.skill.validate_params({"text": "   "})
        self.assertEqual(len(errors), 1)

    def test_coerce_integer_from_string(self):
        fixed, errors = self.skill.validate_params({"text": "x", "repeats": "3"})
        self.assertEqual(errors, [])
        self.assertEqual(fixed["repeats"], 3)
        self.assertIsInstance(fixed["repeats"], int)

    def test_coerce_integer_from_float(self):
        fixed, _ = self.skill.validate_params({"text": "x", "repeats": 3.0})
        self.assertEqual(fixed["repeats"], 3)

    def test_bool_is_not_integer(self):
        # Python bool 是 int 子类——True 硬当 integer 须被拒（语义错误）
        _, errors = self.skill.validate_params({"text": "x", "repeats": True})
        self.assertEqual(len(errors), 1)

    def test_coerce_boolean_from_string(self):
        fixed, errors = self.skill.validate_params({"text": "x", "kp_only": "true"})
        self.assertEqual(errors, [])
        self.assertIs(fixed["kp_only"], True)

    def test_coerce_string_from_int(self):
        fixed, _ = self.skill.validate_params({"text": 123})
        self.assertEqual(fixed["text"], "123")

    def test_uncoercible_integer(self):
        _, errors = self.skill.validate_params({"text": "x", "repeats": "abc"})
        self.assertEqual(len(errors), 1)
        self.assertIn("repeats", errors[0])

    def test_array_not_auto_wrapped(self):
        # string 传给 array 字段：不自动包一层，报错喂回（掩盖形态错误更危险）
        skill = _EchoSkill()
        skill.input_schema = {"type": "object", "required": ["items"],
                              "properties": {"items": {"type": "array"}}}
        _, errors = skill.validate_params({"items": "a,b"})
        self.assertEqual(len(errors), 1)

    def test_enum_reject_and_case_insensitive_fix(self):
        skill = _EchoSkill()
        skill.input_schema = {"type": "object", "required": ["unit_type"],
                              "properties": {"unit_type": {"enum": ["explain", "practice"]}}}
        fixed, errors = skill.validate_params({"unit_type": "Explain"})
        self.assertEqual(errors, [])
        self.assertEqual(fixed["unit_type"], "explain")
        _, errors = skill.validate_params({"unit_type": "quiz"})
        self.assertEqual(len(errors), 1)
        self.assertIn("quiz", errors[0])

    def test_extra_fields_pass_through(self):
        # planner 常带上下文字段（learner_id 等），未声明字段放行
        fixed, errors = self.skill.validate_params(
            {"text": "x", "learner_id": "u1", "role": "learner"})
        self.assertEqual(errors, [])
        self.assertEqual(fixed["learner_id"], "u1")

    def test_non_dict_params(self):
        corrected, errors = self.skill.validate_params("just a string")
        self.assertEqual(corrected, {})
        self.assertEqual(len(errors), 1)
        self.assertIn("JSON 对象", errors[0])

    def test_empty_schema_passes_all(self):
        skill = _EchoSkill()
        skill.input_schema = {}
        fixed, errors = skill.validate_params({"anything": 1})
        self.assertEqual(errors, [])
        self.assertEqual(fixed, {"anything": 1})


class SchemaCompletenessTest(unittest.TestCase):
    """9 技能 schema 完备性快照：input/output 均非空且 type=object（④a 验收）。"""

    def setUp(self):
        self.reg = build_registry()

    def test_all_nine_skills_have_schemas(self):
        self.assertEqual(self.reg.count(), 9)
        for name in self.reg.all_names():
            sk = self.reg.get(name)
            self.assertTrue(sk.input_schema, f"{name} 缺 input_schema")
            self.assertEqual(sk.input_schema.get("type"), "object",
                             f"{name} input_schema 应为 object")
            self.assertTrue(sk.output_schema, f"{name} 缺 output_schema")

    def test_required_fields_match_run_contract(self):
        # 快照锁定：required 集与技能实际契约一致（防 schema 漂移）
        expect = {
            "identify_errors": ["text"],
            "explain_error": ["error"],
            "verify_retell": ["explanation", "key_points", "restatement"],
            "lookup_knowledge_point": [],      # kp_id/keyword 二选一，不设硬 required
            "retrieve_corpus": ["query"],
            "get_review_queue": [],
            "generate_unit": ["unit_type"],
            "web_search": ["query"],
            "parse_document": ["text"],
        }
        for name, req in expect.items():
            self.assertEqual(self.reg.get(name).input_schema.get("required", []),
                             req, f"{name} required 与契约不符")


class PlannerValidationTest(unittest.TestCase):
    """planner 集成：校验失败喂回自纠闭环。"""

    def _make_planner(self, llm_fn):
        probe = _EchoSkill()
        from skills.registry import SkillRegistry
        reg = SkillRegistry()
        reg.register(probe)
        return Planner(reg, llm_call=llm_fn), probe

    def test_validation_error_feeds_back_and_self_corrects(self):
        # 轮1 缺必填 → 技能不执行、错误喂回；轮2 修正 → 执行；轮3 纯 text 收尾
        calls = {"n": 0}

        def llm(messages):
            calls["n"] += 1
            if calls["n"] == 1:
                return json.dumps([
                    {"type": "action", "name": "echo_probe",
                     "params": {"repeats": 1}},
                ], ensure_ascii=False)
            if calls["n"] == 2:
                payload = json.loads(messages[-1]["content"].removeprefix("[tool_result] "))
                assert not payload["result"].get("ok"), "校验失败应 ok=False"
                assert "text" in payload["result"]["error"], "错误消息应指出缺失字段"
                return json.dumps([
                    {"type": "action", "name": "echo_probe",
                     "params": {"text": "修正后的句子", "repeats": "3"}},
                ], ensure_ascii=False)
            return json.dumps([{"type": "text", "content": "已重新调用。"}],
                              ensure_ascii=False)

        planner, probe = self._make_planner(llm)
        r = planner.run("测试")
        self.assertFalse(r["fallback"])
        # 技能只在轮2 执行了一次（轮1 被校验拦截）
        self.assertEqual(len(probe.calls), 1)
        self.assertEqual(probe.calls[0]["text"], "修正后的句子")
        self.assertEqual(probe.calls[0]["repeats"], 3)   # coerce '3'→3 生效
        # trace：轮1 校验失败条目（ok=False）+ 轮2 成功条目
        statuses = [(t["name"], t["ok"]) for t in r["trace"]]
        self.assertEqual(statuses, [("echo_probe", False), ("echo_probe", True)])

    def test_coerced_params_reach_skill(self):
        # 错型（repeats 字符串）被纠正后到达技能，无需 LLM 二轮
        def llm(messages):
            if is_tool_result_message(messages[-1]):
                return json.dumps([{"type": "text", "content": "好的。"}],
                                  ensure_ascii=False)
            return json.dumps([
                {"type": "action", "name": "echo_probe",
                 "params": {"text": "句子", "repeats": "5", "kp_only": "true"}},
            ], ensure_ascii=False)

        planner, probe = self._make_planner(llm)
        r = planner.run("测试")
        self.assertFalse(r["fallback"])
        self.assertEqual(len(probe.calls), 1)
        self.assertEqual(probe.calls[0]["repeats"], 5)
        self.assertIs(probe.calls[0]["kp_only"], True)

    def test_verify_retell_blank_explanation_blocked(self):
        # 行为变化锁定：空 explanation 被校验器拦截（此前技能内部降级 partial）
        from skills.registry import SkillRegistry
        from skills.verify_retell import VerifyRetellSkill
        reg = SkillRegistry()
        reg.register(VerifyRetellSkill())

        def llm(messages):
            if is_tool_result_message(messages[-1]):
                return json.dumps([{"type": "text", "content": "好的。"}],
                                  ensure_ascii=False)
            return json.dumps([
                {"type": "action", "name": "verify_retell",
                 "params": {"explanation": "  ", "key_points": [],
                            "restatement": "复述"}},
            ], ensure_ascii=False)

        p = Planner(reg, llm_call=llm)
        r = p.run("复述一下")
        self.assertFalse(r["fallback"])
        tr = r["trace"][0]
        self.assertFalse(tr["ok"])
        self.assertIn("explanation", tr["result"]["error"])

    def test_unknown_skill_still_reported(self):
        def llm(messages):
            if is_tool_result_message(messages[-1]):
                return json.dumps([{"type": "text", "content": "哦。"}],
                                  ensure_ascii=False)
            return json.dumps([
                {"type": "action", "name": "no_such_skill", "params": {}},
            ], ensure_ascii=False)

        planner, _ = self._make_planner(llm)
        r = planner.run("测试")
        self.assertFalse(r["fallback"])
        self.assertFalse(r["trace"][0]["ok"])
        self.assertIn("未知技能", r["trace"][0]["result"]["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

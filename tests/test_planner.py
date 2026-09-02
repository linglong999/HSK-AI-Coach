# ============================================================
# M4 Planner Loop · 回归测试（tests/test_planner.py）
# 覆盖 M4 验收：自主调技能 / 直接回答 / 6步必终止 / 异常与解析失败不崩
# ============================================================

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills import build_registry
from planner.loop import Planner, is_tool_result_message
from planner.parser import parse_json_array, ParseError
from planner.fallback import fallback_reply


class ParserTest(unittest.TestCase):
    def test_plain_array(self):
        items = parse_json_array(
            '[{"type":"action","name":"identify_errors","params":{"text":"x"}},{"type":"text","content":"hi"}]'
        )
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["type"], "action")
        self.assertEqual(items[1]["type"], "text")

    def test_code_fence_stripped(self):
        items = parse_json_array('```json\n[{"type":"text","content":"hi"}]\n```')
        self.assertEqual(items[0]["content"], "hi")

    def test_filters_invalid_items(self):
        items = parse_json_array(
            '[{"type":"action","name":"a","params":{}},{"foo":1},"bare string"]'
        )
        self.assertEqual(len(items), 1)  # 只保留合法 action/text

    def test_parse_error_raised(self):
        with self.assertRaises(ParseError):
            parse_json_array("完全不是 JSON")

    def test_balanced_braces_truncation(self):
        # 缺收尾括号的自修复
        items = parse_json_array('[{"type":"text","content":"hi"}]')
        self.assertEqual(items[0]["content"], "hi")

    def test_variant_action_as_key(self):
        # 实测 DeepSeek 变体：action 当键名（无 type 字段）→ 归一为标准形态
        items = parse_json_array(
            '[{"action": "identify_errors", "params": {"text": "x"}}]'
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "action")
        self.assertEqual(items[0]["name"], "identify_errors")
        self.assertEqual(items[0]["params"], {"text": "x"})

    def test_variant_text_as_key(self):
        # 实测变体：text 当键名 → 归一为 {"type":"text","content":...}
        items = parse_json_array('[{"text": "你好，我来帮你看看。"}]')
        self.assertEqual(items[0]["type"], "text")
        self.assertEqual(items[0]["content"], "你好，我来帮你看看。")

    def test_variant_action_without_params(self):
        # 变体缺 params → 补空 dict（planner dispatch 层安全）
        items = parse_json_array('[{"action": "get_review_queue"}]')
        self.assertEqual(items[0]["params"], {})

    def test_standard_form_still_wins(self):
        # 标准形态不受归一化影响（含 params 的 action + text 混排）
        items = parse_json_array(
            '[{"type":"action","name":"a","params":{}},{"type":"text","content":"hi"}]'
        )
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["name"], "a")

    def test_bare_object_wrapped(self):
        # 实测 DeepSeek 偶发省略数组包裹：裸对象 → 单元素数组
        items = parse_json_array('{"type": "text", "content": "这句话的英文翻译是：Hello."}')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "text")
        self.assertEqual(items[0]["content"], "这句话的英文翻译是：Hello.")

    def test_bare_object_variant_wrapped(self):
        # 裸对象 + action 键变体 → 归一后单元素
        items = parse_json_array('{"action": "get_review_queue"}')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "action")
        self.assertEqual(items[0]["name"], "get_review_queue")


class ParseRetryTest(unittest.TestCase):
    """M6 真实实跑暴露的协议稳定性：解析失败喂回重试一次 → 裸文本兜底 → fallback。"""

    def setUp(self):
        self.reg = build_registry()

    def _planner(self, outputs):
        """outputs: 依次弹出的原始输出序列（最后一个重复使用）。"""
        seq = list(outputs)

        def mock_llm(messages):
            if len(seq) > 1:
                return seq.pop(0)
            return seq[0]

        return Planner(self.reg, llm_call=mock_llm)

    def test_retry_recovers_to_valid_json(self):
        # 第 1 次纯文本 → 喂回 [format_error] → 第 2 次合法 → 正常完成
        p = self._planner([
            "这句话没有偏误，写得很好。",
            '[{"type": "text", "content": "好的。"}]',
        ])
        r = p.run("测试")
        self.assertFalse(r["fallback"])
        self.assertEqual(r["text"], "好的。")
        self.assertNotIn("protocol_degraded", r)

    def test_bare_text_fallback_after_retry(self):
        # 两次都是自然语言 → 裸文本兜底（protocol_degraded 显式标记，不静默）
        p = self._planner(["这句话没有偏误，写得非常好。"])
        r = p.run("测试")
        self.assertFalse(r["fallback"])
        self.assertEqual(r["text"], "这句话没有偏误，写得非常好。")
        self.assertTrue(r["protocol_degraded"])

    def test_json_fragment_still_falls_back(self):
        # 两次都是 JSON 残片（非自然语言）→ 仍走 fallback（不把语法残片当回复）
        p = self._planner(['[{"type": "text", "content": "截断'])
        r = p.run("测试")
        self.assertTrue(r["fallback"])
        self.assertEqual(r["reason"], "parse")


class PlannerLoopTest(unittest.TestCase):
    def setUp(self):
        self.reg = build_registry()

    def test_agent_calls_skill_then_text(self):
        def mock_llm(messages):
            if not is_tool_result_message(messages[-1]):
                return json.dumps([
                    {"type": "action", "name": "identify_errors",
                     "params": {"text": "我想买苹果很多。"}},
                    {"type": "text", "content": "先检查这句。"},
                ], ensure_ascii=False)
            return json.dumps(
                [{"type": "text", "content": "正确说法是把「很多」放名词前。"}],
                ensure_ascii=False)

        p = Planner(self.reg, llm_call=mock_llm)
        r = p.run("我想买苹果很多。")
        self.assertFalse(r["fallback"])
        self.assertIn("identify_errors", r["used_skills"])
        self.assertLessEqual(r["steps"], 6)
        self.assertIn("很多", r["text"])

    def test_parse_failure_falls_back_not_crash(self):
        p = Planner(self.reg, llm_call=lambda m: "不是JSON")
        r = p.run("hello")
        self.assertTrue(r["fallback"])
        self.assertEqual(r["reason"], "parse")

    def test_max_steps_terminates(self):
        # 无限 action 无 text → 6 步到上限 fallback（不无限循环）
        p = Planner(self.reg,
                    llm_call=lambda m: '[{"type":"action","name":"get_review_queue","params":{}}]')
        r = p.run("复习")
        self.assertTrue(r["fallback"])
        self.assertEqual(r["reason"], "max_steps")

    def test_unknown_skill_does_not_crash(self):
        def mock_llm(messages):
            if not is_tool_result_message(messages[-1]):
                return '[{"type":"action","name":"no_such_skill","params":{}},{"type":"text","content":"查一下"}]'
            return '[{"type":"text","content":"完成"}]'
        p = Planner(self.reg, llm_call=mock_llm)
        r = p.run("试试")
        self.assertFalse(r["fallback"])  # 未知技能走 error 喂回，不崩

    def test_direct_answer_no_skill(self):
        p = Planner(self.reg,
                    llm_call=lambda m: '[{"type":"text","content":"直接回答。"}]')
        r = p.run("你好")
        self.assertFalse(r["fallback"])
        self.assertEqual(r["used_skills"], [])
        self.assertEqual(r["text"], "直接回答。")


class FallbackTest(unittest.TestCase):
    def test_reply_shapes(self):
        r = fallback_reply("parse")
        self.assertTrue(r["fallback"])
        self.assertIn("text", r)


if __name__ == "__main__":
    unittest.main()
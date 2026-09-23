# -*- coding: utf-8 -*-
"""B5 I3 · explainer 事前约束注入（方案 3 事前环）

覆盖：
  - 有图谱+等级 → SYSTEM_PROMPT 注入 b5_scaffold（含目标词、已掌握词集、禁用超纲、
    脚手架档位）；英文最先撤语义出现
  - graph=None → b5_scaffold="（无）"（不误约束，旧调用方零破坏）
  - 等级无法解析（"未知"）→ 不注入
  - explain 讲解主链零改动：格式契约（输出键不变）、注入段追加向后兼容
  - mastered_words 传入 user_level 作为 max_level（等级段过滤）
运行: python -m unittest tests.test_explainer_scaffold -v
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.explainer import Explainer, _fallback_explanation  # noqa: E402


class _FakeGraph:
    """假图谱：get_kp / mastered_words 可控"""
    def __init__(self, mastery=0.5, mastered=None, kp_name=None):
        self._mastery = mastery
        self._mastered = ["好", "对", "是"] if mastered is None else list(mastered)
        self._kp_name = kp_name

    def get_kp(self, kp_id):
        if self._mastery is None:
            return None
        return {"node": {"mastery": self._mastery, "error_count": 2,
                         "knowledge_point": self._kp_name or kp_id}}

    def mastered_words(self, **kw):
        return list(self._mastered)


class B5ScaffoldMethodTest(unittest.TestCase):
    def test_parse_level_int(self):
        self.assertEqual(Explainer._parse_level_int("HSK3"), 3)
        self.assertEqual(Explainer._parse_level_int("3"), 3)
        self.assertEqual(Explainer._parse_level_int("HSK1"), 1)
        self.assertIsNone(Explainer._parse_level_int("未知"))
        self.assertIsNone(Explainer._parse_level_int(""))
        self.assertIsNone(Explainer._parse_level_int("x"))

    def test_graph_none_returns_empty(self):
        self.assertEqual(Explainer._b5_scaffold(None, "kp", "HSK3"), "")

    def test_unknown_level_returns_empty(self):
        g = _FakeGraph(mastery=0.5)
        self.assertEqual(Explainer._b5_scaffold(g, "kp", "未知"), "")

    def test_injects_vocab_and_directive(self):
        g = _FakeGraph(mastery=0.9, mastered=["好", "对"])
        out = Explainer._b5_scaffold(g, "量词", "HSK1")
        self.assertIn("量词", out)          # 目标词
        self.assertIn("HSK1", out)          # 目标等级
        self.assertIn("好", out)            # 已掌握词集注入
        self.assertIn("对", out)
        self.assertIn("禁用超纲词", out)     # 硬约束
        self.assertIn("脚手架档位", out)     # 档位注入

    def test_mastered_limit_passed(self):
        # mastered_words 收到 max_level=user_level
        seen = {}

        class _Probe:
            def __init__(self):
                self._mastery = True

            def get_kp(self, kp_id):
                return {"node": {"mastery": 0.9, "knowledge_point": kp_id}}

            def mastered_words(self, **kw):
                seen.update(kw)
                return ["x"]

        g = _Probe()
        _ = Explainer._b5_scaffold(g, "kp", "HSK3")
        self.assertEqual(seen.get("max_level"), 3)

    def test_empty_mastered_vocab_note(self):
        g = _FakeGraph(mastery=0.5, mastered=[])
        out = Explainer._b5_scaffold(g, "kp", "HSK3")
        self.assertIn("无已掌握词记录", out)


class ExplainerFormatContractTest(unittest.TestCase):
    """讲解主链零改动契约：输出键不变 + format 补全（b5_scaffold 缺省不炸）"""

    def test_output_keys_unchanged(self):
        expl = Explainer(client=None, graph=_FakeGraph())
        # 直接调 format 路径：用假 client 验证注入成功 + 键不变
        captured = {}

        class _C:
            def chat_json_strict(self, system, user, **kw):
                captured["system"] = system
                captured["user"] = user
                return {"explanation": "E", "key_points": [],
                        "keywords": [], "uncertain_note": "", "free_generated": False}

        expl.client = _C()
        err = {"sentence": "我吃了一个苹果。", "fragment": "了",
               "correction": "我吃一个苹果。", "type": "语法",
               "knowledge_point_id": "了", "beyond_level": False}
        r = expl.explain(err, user_level="HSK3", native_lang="zh", graph=_FakeGraph())
        self.assertEqual(sorted(r.keys()),
                         ["explanation", "free_generated", "key_points",
                          "keywords", "uncertain_note"])
        # 中文 prompt 注入 b5_scaffold 且非空
        self.assertIn("禁用超纲词", captured["system"])
        self.assertNotIn("{b5_scaffold}", captured["system"])  # 已填

    def test_format_no_graph_placeholder_filled(self):
        # graph=None → b5_scaffold="（无）"，format 不残留未填充占位符
        class _C:
            def chat_json_strict(self, system, user, **kw):
                self.clean = system
                return {"explanation": "E", "key_points": [],
                        "keywords": [], "uncertain_note": "",
                        "free_generated": False}

        c = _C()
        expl = Explainer(client=c, graph=None)
        err = {"sentence": "S", "fragment": "F", "correction": "C", "type": "语法",
               "knowledge_point_id": "K", "beyond_level": False}
        expl.explain(err, user_level="HSK3", native_lang="zh", graph=None)
        self.assertIn("（无）", c.clean)
        for token in ("{sentence}", "{fragment}", "{b5_scaffold}"):
            self.assertNotIn(token, c.clean)   # 无未填充占位符

    def test_bilingual_injects_english_directive(self):
        class _C:
            def chat_json_strict(self, system, user, **kw):
                self.clean = system
                return {"explanation": "E", "key_points": [],
                        "keywords": [], "uncertain_note": "",
                        "free_generated": False}

        c = _C()
        expl = Explainer(client=c, graph=_FakeGraph(mastery=0.9))
        err = {"sentence": "S", "fragment": "F", "correction": "C", "type": "语法",
               "knowledge_point_id": "K", "beyond_level": False}
        expl.explain(err, user_level="HSK5", native_lang="en", graph=_FakeGraph(mastery=0.9))
        self.assertIn("prohibit overscope words", c.clean)   # 英文约束段


class FallbackTest(unittest.TestCase):
    def test_fallback_unchanged(self):
        # 降级路径不受 B5 影响（键稳定）
        r = _fallback_explanation({"fragment": "了", "correction": "C"}, "")
        self.assertIn("explanation", r)


if __name__ == "__main__":
    unittest.main()
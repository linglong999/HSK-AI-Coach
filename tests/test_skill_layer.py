# ============================================================
# M1 Skill 化 · 核心回归测试（test_skill_layer.py）
# 覆盖：注册表装配 / 双形态(manifest) / 无Key技能 run / 触发评测绿
# 不改引擎；零第三方依赖（用 stdlib unittest + pytest 兼容）
# ============================================================

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from skills import build_registry
from skills.registry import SkillRegistry
from skills.base import Skill, normalize_level_int, normalize_level_label


class LevelNormalizeTest(unittest.TestCase):
    """规范样板：所有带 level 的 skill 共享归一函数，输入形式统一收敛。"""

    def test_int(self):
        self.assertEqual(normalize_level_int(3), 3)
        self.assertEqual(normalize_level_label(3), "HSK3")

    def test_digit_str(self):
        self.assertEqual(normalize_level_int("3"), 3)
        self.assertEqual(normalize_level_label("3"), "HSK3")

    def test_hsk_label(self):
        self.assertEqual(normalize_level_int("HSK4"), 4)
        self.assertEqual(normalize_level_label("HSK4"), "HSK4")

    def test_invalid_falls_to_default(self):
        self.assertEqual(normalize_level_int("foo"), 3)
        self.assertEqual(normalize_level_int("HSK99"), 3)  # 超上界回退
        self.assertEqual(normalize_level_int(None), 3)


class SkillRegistryTest(unittest.TestCase):
    def setUp(self):
        self.reg = build_registry()

    def test_registry_contains_five_core(self):
        names = self.reg.all_names()
        for expect in ["identify_errors", "explain_error", "verify_retell",
                       "lookup_knowledge_point", "get_review_queue"]:
            self.assertIn(expect, names)
        # M9 起注册表在五内核上叠加工具 wrapper（web_search/parse_document）
        self.assertGreaterEqual(len(names), 5)

    def test_list_all_is_compact(self):
        rows = self.reg.list_all()
        self.assertGreaterEqual(len(rows), 5)
        for r in rows:
            self.assertIn("name", r)
            self.assertIn("summary", r)
            self.assertIn("triggers_hint", r)

    def test_duplicate_register_rejected(self):
        from skills.get_review_queue import GetReviewQueueSkill
        with self.assertRaises(ValueError):
            self.reg.register(GetReviewQueueSkill())

    def test_manifest_is_full_four_part(self):
        sk = self.reg.get("identify_errors")
        m = sk.to_manifest()
        for key in ["name", "version", "summary", "triggers", "guardrails",
                    "input_schema", "output_schema"]:
            self.assertIn(key, m)
        self.assertEqual(m["name"], "identify_errors")


class LookupSkillTest(unittest.TestCase):
    def setUp(self):
        self.reg = build_registry()

    def test_lookup_by_kp_id(self):
        res = self.reg.get("lookup_knowledge_point").run({"kp_id": "kp-liangci"})
        self.assertEqual(res["count"], 1)

    def test_lookup_by_keyword_quotes(self):
        # "把"字句 带全角引号，须能按关键词命中；RAG 更宽（count 可>1），首条是 kp-ba-sentence
        res = self.reg.get("lookup_knowledge_point").run({"keyword": "把字句"})
        self.assertGreaterEqual(res["count"], 1)
        self.assertEqual(res["results"][0]["id"], "kp-ba-sentence")

    def test_lookup_unknown(self):
        res = self.reg.get("lookup_knowledge_point").run({"kp_id": "kp-nope"})
        self.assertEqual(res["count"], 0)
        self.assertIn("_error", res)


class ReviewQueueSkillTest(unittest.TestCase):
    def setUp(self):
        self.reg = build_registry()

    def test_empty_graph_returns_empty(self):
        res = self.reg.get("get_review_queue").run({})
        self.assertEqual(res["count"], 0)
        self.assertEqual(res["items"], [])


class TriggerEvalTest(unittest.TestCase):
    """触发评测（规则层近似）必须保持 12/12 绿——回归护栏。"""

    def _load_rule_predict(self):
        import importlib.util
        ev_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "datasets", "eval", "run_skill_trigger_eval.py")
        spec = importlib.util.spec_from_file_location("run_skill_trigger_eval", ev_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_trigger_eval_green(self):
        mod = self._load_rule_predict()
        with open(mod.EVAL_PATH, encoding="utf-8") as f:
            data = json.load(f)
        total = passed = 0
        for group, gdata in data["groups"].items():
            for case in gdata.get("cases", []):
                predicted = mod.rule_predict(case["query"])
                res = mod._assert_trigger(predicted, case["expect"], group)
                total += 1
                passed += 1 if res["pass"] else 0
        self.assertEqual(passed, total, f"触发评测应全绿, 失败 {total - passed}/{total}")


if __name__ == "__main__":
    unittest.main()
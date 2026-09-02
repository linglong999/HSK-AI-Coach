# ============================================================
# M6 自由对话评测 · 回归测试（tests/test_m6.py）
# 覆盖：judge 判分结构乙 / 解析失败不记入 / 阈值代码层 / 无 Key skip
# ============================================================

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.judge import Judge


class TestJudge(unittest.TestCase):
    def setUp(self):
        self.judge = Judge()

    def test_structure_scorable(self):
        j = self.judge._unscorable(10, reason="parse_fail")
        self.assertIn("score", j)
        self.assertIn("maxScore", j)
        self.assertIn("passed", j)
        self.assertIn("comment", j)
        self.assertIn("strengths", j)
        self.assertIn("improvements", j)
        self.assertIn("scorable", j)

    def test_no_key_skip_not_forge(self):
        # mock llm 抛 RuntimeError（模拟无 Key）
        j = Judge(llm=lambda m: (_ for _ in ()).throw(RuntimeError("no key")))
        r = j.run("题", "答")
        self.assertFalse(r["scorable"])
        self.assertEqual(r["reason"], "no_key")
        self.assertIsNone(r["score"])  # 不伪造分数

    def test_parse_failure_not_scored(self):
        j = Judge(llm=lambda m: "完全不是 JSON")
        r = j.run("题", "答")
        self.assertFalse(r["scorable"])
        self.assertEqual(r["reason"], "parse_fail")
        self.assertIsNone(r["score"])  # 不臆造一半分

    def test_pass_threshold_code_level(self):
        # 注入可解析 JSON，验证 passed 由代码算
        def llm(m):
            return '{"score":8,"comment":"好","strengths":["S"],"improvements":["I"]}'
        j = Judge(llm=llm)
        r = j.run("题", "答", max_score=10)
        self.assertTrue(r["scorable"])
        self.assertEqual(r["score"], 8)
        self.assertTrue(r["passed"])       # 8/10 >= 0.8
        self.assertEqual(r["strengths"], ["S"])

        def llm2(m):
            return '{"score":7,"comment":"中","strengths":[],"improvements":[]}'
        r2 = Judge(llm=llm2).run("题", "答", max_score=10)
        self.assertFalse(r2["passed"])     # 7/10 < 0.8

    def test_score_clamped(self):
        def llm(m):
            return '{"score":200,"comment":"x"}'
        r = Judge(llm=llm).run("题", "答", max_score=10)
        self.assertEqual(r["score"], 10)   # clamp 到 maxScore

    def test_parse_extracts_from_fence(self):
        def llm(m):
            return '```json\n{"score":9,"comment":"好"}\n```'
        r = Judge(llm=llm).run("题", "答", max_score=10)
        self.assertTrue(r["scorable"])
        self.assertEqual(r["score"], 9)


if __name__ == "__main__":
    unittest.main()
# -*- coding: utf-8 -*-
"""防回退静态断言：识别 prompt 的隐性偏误 few-shot 覆盖（0.25 收尾）。

动机：'吃奶茶'搭配、数量词'很多'句尾语序这两类隐性偏误，LLM 若无 few-shot
引导会漏检，导致 has_err=False、介入层无提示（用户实机复现过）。若该 few-shot
被误删，覆盖率静默回退——用静态断言锁定关键词。"""
import unittest


class RecognizerFewShotCoverageTest(unittest.TestCase):

    def test_implicit_error_fewshots_present(self):
        from engine.recognizer import SYSTEM_PROMPT
        # 例4：液体饮品用"喝"
        self.assertIn("液体饮品", SYSTEM_PROMPT)
        self.assertIn("奶茶", SYSTEM_PROMPT)
        # 例5：数量词"很多"作定语后置
        self.assertIn("作定语却后置", SYSTEM_PROMPT)
        self.assertIn("很多奶茶", SYSTEM_PROMPT)

    def test_recall_corner_cases_kept(self):
        # 既有少数样本护栏（量词、把字句边界）仍不应丢失
        from engine.recognizer import SYSTEM_PROMPT
        self.assertIn("一个手机", SYSTEM_PROMPT)   # 例2 量词
        self.assertIn("几岁", SYSTEM_PROMPT)        # 例3 语体


if __name__ == "__main__":
    unittest.main()
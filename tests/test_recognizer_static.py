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
        self.assertIn("一个手机", SYSTEM_PROMPT)   # 例1 量词
        self.assertIn("几岁", SYSTEM_PROMPT)        # 例2 语体

    def test_wrong_ba_example_removed(self):
        # A2-1：例1「这个好吃吧？」整条撤除——该 few-shot 判定条件与示例自相矛盾
        from engine.recognizer import SYSTEM_PROMPT
        self.assertNotIn("这个好吃吧", SYSTEM_PROMPT)
        # 例5 低置信示例存在（新增）；"吧"语用示例已不再以"吧"为核心
        self.assertIn("例5（低置信示例", SYSTEM_PROMPT)

    def test_id_hard_constraint_and_fewshot_ids(self):
        from engine.recognizer import SYSTEM_PROMPT
        # A2-3：填 id 硬约束——清单有对应项必填
        self.assertIn("清单中存在对应项时必填该 id", SYSTEM_PROMPT)
        self.assertIn("宁可留空也不要臆造清单外的 id", SYSTEM_PROMPT)
        # 例1 量词 / 例4 后置 均已挂上知识 id（修复"多数留空"倾向）
        self.assertIn("knowledge_point_id\":\"kp-liangci\"", SYSTEM_PROMPT)
        self.assertIn("knowledge_point_id\":\"kp-zhuangyu-chezhi\"", SYSTEM_PROMPT)

    def test_e2_fragment_covers_you(self):
        # E-2：例3 fragment 圈「你几岁」整段一条报（"你→您"不再另立条目）
        from engine.recognizer import SYSTEM_PROMPT
        self.assertIn("fragment\":\"你几岁\"", SYSTEM_PROMPT)
        self.assertIn("correction\":\"您多大年纪\"", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
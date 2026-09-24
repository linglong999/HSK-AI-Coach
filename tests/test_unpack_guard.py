# -*- coding: utf-8 -*-
"""B5 I4 · 事后拦截环 engine/unpack_guard.py（方案 3 事后环）

覆盖：
  - 无违规 → ok=True、generate 只调 1 次
  - 违规 → 重试注入违规清单（generate 第二次入参带违规清单）；retries 后仍违规
    → 降级标注 degraded_overscope（非无限重试：mock 恒产违规词，断言只调 2 次）
  - check_unpack 纯判定：违规清单 {word, level}、空文本 ok
  - detect_beyond_level 复用零改动（同输入同输出比对 guard 与 recognizer 直调）
运行: python -m unittest tests.test_unpack_guard -v
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.recognizer import detect_beyond_level  # noqa: E402
from engine.unpack_guard import check_unpack, guard_unpack  # noqa: E402


# 构造词表：learner_level=2，包含超纲词（level>3，宽容相邻一级）
# 需含"经济">3 → 适度。选 哲学=6（>3 → 超纲）、历史=2（≤3 → 不超）
_LEX = {
    "word_level": {"经济": 2, "历史": 2, "哲学": 6, "研究": 4, "我": 1},
    "char_level": {},
}


class CheckUnpackTest(unittest.TestCase):
    def test_no_violation_ok(self):
        r = check_unpack("我学习历史和经济学", 2, _LEX)
        self.assertTrue(r["ok"])
        self.assertEqual(r["violations"], [])

    def test_with_violation(self):
        r = check_unpack("我研究哲学", 2, _LEX)
        self.assertFalse(r["ok"])
        self.assertTrue(any(v["word"] == "哲学" and v["level"] == 6
                            for v in r["violations"]))

    def test_empty_text_ok(self):
        r = check_unpack("", 2, _LEX)
        self.assertTrue(r["ok"])

    def test_matches_detect_beyond_level(self):
        # 复用零改动：guard 判定与 recognizer 直调同输出
        text = "我研究哲学和历史"
        violations = detect_beyond_level(text, 2, _LEX)
        r = check_unpack(text, 2, _LEX)
        self.assertEqual(r["ok"], not violations)
        self.assertEqual({v["word"] for v in r["violations"]},
                         {v["word"] for v in violations})


class GuardUnpackTest(unittest.TestCase):
    def test_clean_generate_ok_once(self):
        calls = []

        def gen(pending):
            calls.append(list(pending))
            return {"explanation": "我学经济学和历史学"}

        r = guard_unpack(gen, 2, _LEX)
        self.assertTrue(r["ok"])
        self.assertEqual(r["attempts"], 1)
        self.assertEqual(len(calls), 1)       # 只生成一次
        self.assertEqual(calls[0], [])        # 首轮无违规注入

    def test_violation_then_retry_inject(self):
        # 首次产违规 → 重试注入违规清单；第二次清洁 → ok
        outcomes = ["我研究哲学", "我学经济"]
        injected = []

        def gen(pending):
            injected.append(list(pending))
            return {"explanation": outcomes.pop(0)}

        r = guard_unpack(gen, 2, _LEX)
        self.assertTrue(r["ok"])
        self.assertEqual(r["attempts"], 2)
        self.assertEqual(len(injected), 2)
        self.assertEqual(injected[0], [])                     # 首轮无清单
        self.assertTrue(any(v["word"] == "哲学" for v in injected[1]))  # 二次带违规

    def test_persistent_violation_degrade_no_infinite(self):
        # mock 恒产违规词 → 只调 retries+1=2 次，进入降级标注（非无限重试）
        calls = []

        def gen(pending):
            calls.append(list(pending))
            return {"explanation": "我研究哲学"}

        r = guard_unpack(gen, 2, _LEX)
        self.assertFalse(r["ok"])
        self.assertEqual(len(calls), 2)       # 首轮 + 重试 1 次 = 2，不无限
        self.assertEqual(calls[0], [])        # 首轮无清单
        self.assertTrue(any(v["word"] == "哲学" for v in calls[1]))  # 重试带违规
        self.assertEqual(r["attempts"], 2)
        self.assertTrue(r["result"].get("degraded_overscope"))
        self.assertTrue(r["result"].get("overscope_violations"))
        self.assertTrue(any(v["word"] == "哲学" for v in r["violations"]))

    def test_custom_retries(self):
        def gen(pending):
            return {"explanation": "我研究哲学"}

        r = guard_unpack(gen, 2, _LEX, retries=3)
        self.assertFalse(r["ok"])
        self.assertEqual(r["attempts"], 4)   # retries+1=4 次后降级

    def test_result_passthrough(self):
        def gen(pending):
            return {"explanation": "我学经济", "custom": 42}

        r = guard_unpack(gen, 2, _LEX)
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"]["custom"], 42)


class RouterIntegrationTest(unittest.TestCase):
    """B5 I6 · router.process 讲解出口同过守门（两讲解出口之一，超纲重试→降级标注）"""

    LEARNER = "test_router_guard"

    def setUp(self):
        from engine.router import Router
        # user_level=HSK2（宽容相邻 >3 才算超纲）→ 词表中 level=4 的"历史"即超纲
        self.router = Router(learner_id=self.LEARNER, native_lang="英语",
                             user_level="HSK2")
        self.data_path = os.path.join(_PROJECT_ROOT, "data",
                                      f"graph_{self.LEARNER}.json")
        # mock 识别：返回一条已确认偏误（走讲解出口）
        self.router.recognizer.recognize = (
            lambda text, level=3, native_lang="", config=None: {
                "errors": [{
                    "fragment": "苹果很多", "correction": "很多苹果",
                    "type": "语法", "type_confident": True, "confidence": 0.9,
                    "knowledge_point_id": "kp-test-guard",
                }], "uncertain": []})

    def tearDown(self):
        if os.path.exists(self.data_path):
            os.remove(self.data_path)

    def _mock_explain(self):
        """恒产超纲词（首轮与重试注入后都产违规）→ 必触降级；记录是否收到重试注入。"""
        got_retry = {"v": False}

        def _expl(err, **kw):
            if "_b5_guard_violations" in err:
                got_retry["v"] = True
            # B8 换源 3.0：讲解含"提到/知识"=4 级，HSK2 宽容相邻 >3 → 仍判超纲；
            # "历史"3.0=3 已不触发（2021 版为 4，等级漂移见 tests/test_lexicon3.py）
            return {"explanation": "讲解提到了历史这个知识点。",
                    "key_points": [], "keywords": [],
                    "uncertain_note": "", "free_generated": False}

        self.router.explainer.explain = _expl
        return got_retry

    def test_overscope_degrade_marked(self):
        got = self._mock_explain()
        r = self.router.process("我吃了苹果。")
        expl = r["errors"][0]["explanation"]
        self.assertTrue(expl.get("degraded_overscope"))   # 恒产超纲 → 降级标注
        self.assertTrue(expl.get("overscope_violations"))  # 违规清单透出
        self.assertTrue(got["v"])   # 重试注入发生过

    def test_clean_explanation_passthrough(self):
        def _expl(err, **kw):
            return {"explanation": "语序是很多加名词，如：很多苹果。",
                    "key_points": [], "keywords": [],
                    "uncertain_note": "", "free_generated": False}
        self.router.explainer.explain = _expl
        r = self.router.process("我吃了苹果。")
        expl = r["errors"][0]["explanation"]
        self.assertNotIn("degraded_overscope", expl)   # 无违规 → 不降级
        self.assertIn("很多苹果", expl["explanation"])


if __name__ == "__main__":
    unittest.main()
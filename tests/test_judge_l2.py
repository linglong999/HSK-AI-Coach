# -*- coding: utf-8 -*-
# tests/test_judge_l2.py —— B6 J2 · L2 LLM-judge 接入单测
# 覆盖：JSON 解析（合法/围栏/非法/缺维/越界）、双门槛分离计算、
#      schema 非法走 PENDING（不静默 fail）、client 调用异常→PENDING、
#      无 client 降级 skipped。既有测试零改动全绿。

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from datasets.eval.tutor_quality.judge import (  # noqa: E402
    CaseResult, _l2_llm_judge, _l2_real_judge, _parse_l2_json)


def _case(**over):
    c = {"id": "TQ-NAT-001", "case_type": "naturalness_anchor",
         "input": "（点餐）你要点什么？", "context": None,
         "expected_behavior": {"detect": None}, "redlines": [],
         "gold_judge_hint": None, "metadata": {"learner_level": 3}}
    c.update(over)
    return c


def _rc(case=None, redline=None):
    rc = CaseResult(case or _case())
    rc.detected = []
    rc.degraded = []
    rc.meta = {}
    rc.trace = {"errors": []}
    rc.redline_fails = [redline] if redline else []
    return rc


class FakeClient:
    """确定性 mock：记录最近一次 temperature，返回预设 raw。"""
    def __init__(self, raw):
        self.raw = raw
        self.last_temperature = None

    def complete(self, prompt, temperature=0, model=""):
        self.last_temperature = temperature
        return self.raw


class ParseL2Test(unittest.TestCase):
    def test_valid_json(self):
        raw = ('{"per_dim":{"1":5,"2":4,"3":5,"4":4,"5":4,"6":4,'
               '"7":5,"8":4,"9":5,"10":5}, "reason":"ok"}')
        p = _parse_l2_json(raw)
        self.assertIsNotNone(p)
        self.assertEqual(p["per_dim"][1], 5)
        self.assertEqual(p["per_dim"][10], 5)

    def test_fenced_json(self):
        raw = ('```json\n{"per_dim":{"1":1,"2":1,"3":1,"4":1,"5":1,"6":1,'
               '"7":1,"8":1,"9":1,"10":1}}\n```')
        self.assertIsNotNone(_parse_l2_json(raw))

    def test_invalid_json(self):
        self.assertIsNone(_parse_l2_json("这不是 json"))
        self.assertIsNone(_parse_l2_json(""))
        self.assertIsNone(_parse_l2_json("12345"))

    def test_missing_dims(self):
        raw = '{"per_dim":{"1":5,"2":5}}'
        self.assertIsNone(_parse_l2_json(raw))

    def test_out_of_range(self):
        raw = ('{"per_dim":{"1":9,"2":5,"3":5,"4":5,"5":5,"6":5,'
               '"7":5,"8":5,"9":5,"10":5}}')
        self.assertIsNone(_parse_l2_json(raw))

    def test_nonint_dim(self):
        raw = ('{"per_dim":{"1":"5","2":5,"3":5,"4":5,"5":5,"6":5,'
               '"7":5,"8":5,"9":5,"10":5}}')
        self.assertIsNone(_parse_l2_json(raw))


class LlmJudgeTest(unittest.TestCase):
    def test_quality_and_naturalness_split(self):
        # 教学质量 6 维全 2（低）、自然度 4 维全 5（高）→ 两均值分列不混淆
        raw = ('{"per_dim":{"1":2,"2":2,"3":2,"4":2,"5":2,"6":2,'
               '"7":5,"8":5,"9":5,"10":5},"reason":"r"}')
        out = _l2_llm_judge(_case(), _rc(), FakeClient(raw))
        self.assertFalse(out["pending"])
        self.assertEqual(out["quality_mean"], 2.0)
        self.assertEqual(out["naturalness_mean"], 5.0)
        self.assertEqual(out["verdict"], "pass")

    def test_temperature_zero_pinned(self):
        raw = ('{"per_dim":{"1":4,"2":4,"3":4,"4":4,"5":4,"6":4,'
               '"7":4,"8":4,"9":4,"10":4},"reason":"r"}')
        c = FakeClient(raw)
        _l2_llm_judge(_case(), _rc(), c, judge_model="qwen")
        self.assertEqual(c.last_temperature, 0)

    def test_schema_invalid_goes_pending_not_fail(self):
        out = _l2_llm_judge(_case(), _rc(), FakeClient("not json at all"))
        self.assertTrue(out["pending"])
        self.assertIsNone(out["verdict"])     # 不静默给 fail/不遮成 pass
        self.assertIsNone(out["per_dim"])

    def test_client_raises_goes_pending(self):
        class BoomClient(FakeClient):
            def complete(self, *a, **k):
                raise RuntimeError("provider down")
        out = _l2_llm_judge(_case(), _rc(), BoomClient(""))
        self.assertTrue(out["pending"])
        self.assertIn("LLM 调用失败", out["reason"])

    def test_verdict_fail_when_redline(self):
        raw = ('{"per_dim":{"1":2,"2":2,"3":2,"4":2,"5":2,"6":2,'
               '"7":2,"8":2,"9":2,"10":2},"reason":"r"}')
        out = _l2_llm_judge(_case(), _rc(redline="R2"), FakeClient(raw))
        self.assertEqual(out["verdict"], "fail")


class RealJudgeDispatchTest(unittest.TestCase):
    def test_no_client_degrades_to_skipped(self):
        out = _l2_real_judge(_case(), _rc(), None)
        self.assertTrue(out.get("skipped"))
        self.assertIsNone(out["verdict"])

    def test_with_client_runs_real(self):
        raw = ('{"per_dim":{"1":4,"2":4,"3":4,"4":4,"5":4,"6":4,'
               '"7":4,"8":4,"9":4,"10":4},"reason":"r"}')
        out = _l2_real_judge(_case(), _rc(), FakeClient(raw), judge_model="qwen")
        self.assertFalse(out.get("skipped"))
        self.assertEqual(out["verdict"], "pass")


if __name__ == "__main__":
    unittest.main()
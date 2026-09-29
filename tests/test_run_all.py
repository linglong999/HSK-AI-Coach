# -*- coding: utf-8 -*-
"""B6 J5 · run_all 单入口分层回归测试。

覆盖（B6 测试设计）：
- suites 收集完整性：golden 语料文件全注册、deterministic/llm_judge 两组成分正确。
- deterministic 层 mock 跑 exit 0（PR gate 零 LLM、红线全护）。
- 构造 fail suite → run_all exit 1（CI 阻塞语义）。
- baseline delta 门控触发（J6 统计门控：naturalness_mean 超带宽 → llm_judge suite fail）。
- 无 LLM client 时 L2 NOT_RUN 不误阻塞确定性层。
"""
import json
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # HSK-AI-Coach
_EVAL = os.path.join(_ROOT, "datasets", "eval")
for p in (_ROOT, _EVAL):
    if p not in sys.path:
        sys.path.insert(0, p)
from datasets.eval import run_all  # noqa: E402


def _fake_client(per_dim):
    """L2 judge 的假 LLM client：恒返固定 10 维（temperature 契约对齐）。"""
    dims = "{" + ",".join(f'"{k}": {v}' for k, v in per_dim.items()) + "}"
    body = '{"per_dim": %s, "reason": "ok"}' % dims

    class Fake:
        def complete(self, prompt, temperature=0, model=""):
            assert temperature == 0  # B6-A CI 稳定性：temperature 固定
            return body
    return Fake()


GOOD_10 = {str(i): 5 for i in range(1, 11)}         # 教学质量+自然度全 5
LOW_NAT = dict(GOOD_10); LOW_NAT.update({"7": 1, "8": 1, "9": 1, "10": 1})  # 自然度 1

FAKE_CASE = {
    "id": "TQ-X1", "source": "golden_v1_4", "source_id": "x1",
    "case_type": "explain", "input": "我把咖啡喝了。", "context": None,
    "expected_behavior": {"detect": None, "must_explain": False, "must_verify": False},
    "redlines": [], "metadata": {"learner_level": None, "native_lang": "英语"},
}


class TestSuiteRegistration(unittest.TestCase):
    def test_corpora_files_registered(self):
        # golden 语料文件全注册（收集完整性标定）
        for key in ("golden", "retell", "cged", "skill"):
            self.assertTrue(os.path.exists(run_all.CORPORA[key]),
                            f"{key} 语料未注册/缺失")
        self.assertTrue(os.path.exists(run_all.TUTOR_CASES))

    def test_deterministic_suite_membership(self):
        names = set(run_all.DETERMINISTIC_SUITES)
        self.assertTrue({"tutor_l1_l3", "golden_corpus", "retell_corpus",
                         "cged_corpus", "skill_corpus", "d6_assertions"} <= names)

    def test_hardcoded_redlines_in_deterministic(self):
        # 红线全放 deterministic 层（J5 语义）：llm_judge 不承担红线计数任务
        self.assertIn("tutor_l1_l3", run_all.DETERMINISTIC_SUITES)


class TestRunAllDeterministic(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        run_all.set_judge_client(None)

    def test_deterministic_runs_zero_llm_exit0(self):
        # 即使本机配置了 Key，也不允许确定性闸门触网或读写真实图谱。
        with mock.patch("engine.llm.client.LLMClient._openai",
                        side_effect=AssertionError("离线回归不应创建模型连接")), \
             mock.patch("engine.graph.store.GraphStore.load",
                        side_effect=AssertionError("离线回归不应读取用户图谱")), \
             mock.patch("engine.graph.store.GraphStore.save",
                        side_effect=AssertionError("离线回归不应保存用户图谱")):
            code, suites = run_all.run("deterministic")
        self.assertEqual(code, 0)
        self.assertTrue(all(v["ok"] for v in suites.values()))

    def test_fail_suite_exits_1(self):
        # 构造一个 fail suite → exit 1（CI 阻塞语义）
        with mock.patch.dict(run_all.DETERMINISTIC_SUITES,
                             {"golden_corpus": lambda: {"ok": False, "detail": {}}}):
            code, suites = run_all.run("deterministic")
        self.assertEqual(code, 1)
        self.assertFalse(suites["golden_corpus"]["ok"])


class TestBaselineGate(unittest.TestCase):
    def test_naturalness_overshoot_fails_gate(self):
        # naturalness_mean=5 vs baseline=4.0 band=0.3 → delta 1.0 超带宽 → ok=False
        with mock.patch("tutor_quality.judge.load_cases", return_value=[FAKE_CASE]), \
             mock.patch.object(run_all, "_load_baseline",
                               return_value={"naturalness_mean": 4.0,
                                             "pass_rate": 1.0, "band": 0.3}):
            run_all.set_judge_client(_fake_client(LOW_NAT))
            code, suites = run_all.run("llm_judge")
        self.assertEqual(code, 1)
        det = suites["tutor_l2_real"]["detail"]
        self.assertFalse(det["delta_ok"])
        self.assertLessEqual(det["naturalness_mean"], 4.0 - 0.3)  # 实测自然度确低

    def test_within_band_passes(self):
        with mock.patch("tutor_quality.judge.load_cases", return_value=[FAKE_CASE]), \
             mock.patch.object(run_all, "_load_baseline",
                               return_value={"naturalness_mean": 5.0,
                                             "pass_rate": 1.0, "band": 0.3}):
            run_all.set_judge_client(_fake_client(GOOD_10))
            code, suites = run_all.run("llm_judge")
        self.assertEqual(code, 0)
        self.assertTrue(suites["tutor_l2_real"]["detail"]["delta_ok"])

    def test_no_client_is_not_run_not_blocking(self):
        # 无 LLM client → L2 NOT_RUN（不因缺 Key 阻塞，detail 明示）
        run_all.set_judge_client(None)
        _, suites = run_all.run("llm_judge")
        det = suites["tutor_l2_real"]
        self.assertTrue(det["ok"])
        self.assertTrue(det["skipped"])


class TestPinMeta(unittest.TestCase):
    def test_pin_contains_triple(self):
        pin = run_all._pin_meta("qwen-judge")
        self.assertEqual(pin["judge_model_id"], "qwen-judge")
        self.assertEqual(len(pin["prompt_template_hash"]), 8)
        self.assertTrue(pin["rubric_version"])  # rubric.md 文首 rubric_version=v2


if __name__ == "__main__":
    unittest.main()

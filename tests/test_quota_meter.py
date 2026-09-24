# ============================================================
# tests/test_quota_meter.py · B7 S2 · token 线性计量 + 能量折算
# 覆盖：
#   A. TokenMeter：input/output 线性折算、零/负/缺 token → 0 分、自定义费率
#   B. RoundUsage + begin_round/report_usage/end_round：跨多次 report 累计、入坑清坑
#   C. error_ledger token_usage 事件：record_token_usage 落账 + evidence 结构化 + KINDS 校验
#   D. _account_energy：成功有产出 → 折算+记账+回填 energy_used；
#      失败(status!=200) / 零产出(全 tr.ok=false) / 零用量回报 → 一律不入账不扣
# 运行: python -m unittest tests.test_quota_meter -v
# ============================================================

import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine.dialog_service import DialogService
from engine.graph.error_graph import ErrorGraph
from engine.memory.error_ledger import ErrorLedger, KINDS
from engine.quota import (QUOTA_PARAMS, RoundUsage, TokenMeter,
                          begin_round, end_round, report_usage)


# ---------------- A · TokenMeter ----------------

class TokenMeterTest(unittest.TestCase):

    def test_linear_input_output(self):
        m = TokenMeter({"energy_per_1k_input": 1.0,
                        "energy_per_1k_output": 2.0})
        # 2000 in ×1/千 + 1000 out ×2/千 = 2 + 2 = 4
        self.assertAlmostEqual(m.energy(2000, 1000), 4.0, places=3)
        # 纯输出占比更高
        self.assertAlmostEqual(m.energy(0, 500), 1.0, places=3)
        self.assertAlmostEqual(m.energy(500, 0), 0.5, places=3)

    def test_zero_and_negative_and_missing_count_as_zero(self):
        m = TokenMeter()
        self.assertEqual(m.energy(None, None), 0.0)
        self.assertEqual(m.energy(0, 0), 0.0)
        self.assertEqual(m.energy(-5, -3), 0.0)
        self.assertEqual(m.energy(-5, 1000), 2.0)   # 负数输入按 0，输出照算

    def test_custom_params_and_defaults(self):
        m1 = TokenMeter({"energy_per_1k_input": 3.0,
                         "energy_per_1k_output": 5.0})
        self.assertAlmostEqual(m1.energy(1000, 1000), 8.0, places=3)
        # 缺补贴默认费率 = QUOTA_PARAMS
        m2 = TokenMeter()
        self.assertAlmostEqual(
            m2.energy(1000, 1000),
            float(QUOTA_PARAMS["energy_per_1k_input"])
            + float(QUOTA_PARAMS["energy_per_1k_output"]), places=3)

    def test_energy_from_usage_dict(self):
        m = TokenMeter()
        self.almostEqual = self.assertAlmostEqual
        self.assertAlmostEqual(
            m.energy_from_usage({"prompt_tokens": 2000,
                                 "completion_tokens": 1000}), 4.0, places=3)
        self.assertEqual(m.energy_from_usage(None), 0.0)
        self.assertEqual(m.energy_from_usage({}), 0.0)


# ---------------- B · RoundUsage 收集器 ----------------

class RoundUsageTest(unittest.TestCase):

    def setUp(self):
        end_round()            # 清掉可能残留的 context（各用例间隔离）

    def tearDown(self):
        end_round()

    def test_accumulate_across_reports(self):
        ru = RoundUsage()
        ru.add({"prompt_tokens": 100, "completion_tokens": 50})
        ru.add({"prompt_tokens": 60, "completion_tokens": 90})
        self.assertEqual(ru.totals(), (160, 140))
        self.assertEqual(ru.calls, 2)

    def test_report_usage_writes_to_current_round(self):
        begin_round()
        try:
            report_usage({"prompt_tokens": 10, "completion_tokens": 20})
            report_usage({"prompt_tokens": 5, "completion_tokens": 0})
            report_usage(None)                       # 失败/无 usage → 忽略
            report_usage({"prompt_tokens": 0, "completion_tokens": 0})
            ru = end_round()
            self.assertEqual(ru.totals(), (15, 20))
        finally:
            end_round()

    def test_end_round_clears_context(self):
        begin_round()
        report_usage({"prompt_tokens": 9, "completion_tokens": 9})
        ru = end_round()
        self.assertEqual(ru.totals(), (9, 9))
        root = end_round()
        self.assertIsNone(root)                       # 已清空，二次取回 None


# ---------------- C · ledger token_usage 事件 ----------------

class TokenUsageLedgerTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.led = ErrorLedger("energy_user", root=self.root)

    def test_kinds_contains_token_usage(self):
        self.assertIn("token_usage", KINDS)

    def test_record_token_usage_writes_event(self):
        self.led.record_token_usage(1000, 500, energy=2.0,
                                    evidence="conv=c1")
        evs = self.led.recent()
        self.assertEqual(len(evs), 1)
        e = evs[0]
        self.assertEqual(e["kind"], "token_usage")      # 不被误改成 repeated_error
        self.assertEqual(e["kp_id"], "__round_energy__")
        self.assertIn("prompt_tokens=1000", e["evidence"])
        self.assertIn("completion_tokens=500", e["evidence"])
        self.assertIn("energy=2.000", e["evidence"])
        self.assertIn("conv=c1", e["evidence"])


# ---------------- D · _account_energy 不入账规则 ----------------

class _FakeRouter:
    learner_id = "eng_user"

    def __init__(self):
        self.graph = ErrorGraph("eng_graph")
        self.recognizer = None
        self.verifier = None
        self.explainer = None


def _svc(tmp):
    return DialogService(_FakeRouter(), memory_root=tmp)


def _usage(prompt, completion):
    from engine.quota import RoundUsage
    ru = RoundUsage()
    ru.add({"prompt_tokens": prompt, "completion_tokens": completion})
    return ru


class AccountEnergyTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.svc = _svc(self.tmp)

    def _ledger(self):
        return self.svc.get_writeback("eng_user").ledger

    def test_success_with_usage_meters_and_records(self):
        out = {"trace": [{"name": "t", "ok": True}], "conversation_id": "c1"}
        ctx = {"learner_id": "eng_user", "conversation_id": "c1"}
        self.svc._account_energy(ctx, out, 200, _usage(2000, 1000), None)
        self.assertIn("energy_used", out)              # 回填 SSE done
        self.assertGreater(out["energy_used"], 0)
        evs = self._ledger().recent()
        self.assertEqual(evs[0]["kind"], "token_usage")

    def test_hard_failure_status_non200_no_account(self):
        out = {"trace": [{"name": "t", "ok": True}], "conversation_id": "c1"}
        self.svc._account_energy({}, out, 500, _usage(2000, 1000), None)
        self.assertEqual(self._ledger().recent(), [])

    def test_zero_output_no_account(self):
        # 全 fallback / 无产出（tr.ok 均 False）→ 不入账不扣
        out = {"trace": [{"name": "t", "ok": False}], "conversation_id": "c1"}
        self.svc._account_energy({}, out, 200, _usage(2000, 1000), None)
        self.assertEqual(self._ledger().recent(), [])
        self.assertNotIn("energy_used", out)

    def test_zero_usage_report_no_account(self):
        # 无 LLM 用量回报（如 mock 直答文本）→ 0 分不记账
        out = {"trace": [{"name": "t", "ok": True}], "conversation_id": "c1"}
        self.svc._account_energy({}, out, 200, _usage(0, 0), None)
        self.assertEqual(self._ledger().recent(), [])
        self.assertNotIn("energy_used", out)

    def test_usage_none_no_account(self):
        out = {"trace": [{"name": "t", "ok": True}], "conversation_id": "c1"}
        self.svc._account_energy({}, out, 200, None, None)
        self.assertEqual(self._ledger().recent(), [])


if __name__ == "__main__":
    unittest.main()
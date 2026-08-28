# ============================================================
# 4.2 降级兜底测试（验收：模拟降级场景工程不崩溃、有合理兜底输出）
# 全部 mock，不调用 LLM、不需要真实 API Key。
# 覆盖：
#   1. LLM 网络瞬时故障退避重试（第3次成功 / 彻底失败抛）
#   2. HTTP 4xx（Key 无效）不重试直接抛
#   3. 识别引擎不可用 → 规则回退（超纲词候选进 uncertain，绝不产 confirmed）
#   4. 连续验证不过 → N=2 retry_simpler / N=4 suggest_teacher
#   5. 不同任务失败不串计数（回归：修复实例级计数 bug）
# 运行: python -m unittest tests.test_degradation_fallback -v
# ============================================================

import os
import sys
import unittest
from unittest.mock import MagicMock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import engine.llm.client as client_mod
from engine.llm.client import LLMClient
from engine.recognizer import Recognizer
from engine.verifier import Verifier

import config.settings as settings


class FakeResp:
    def __init__(self, body: dict):
        import json
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok_body():
    return {"choices": [{"message": {"content": "hello"}}]}


class ClientRetryTestBase(unittest.TestCase):
    def setUp(self):
        self._orig_cfg = settings.get_llm_config
        settings.get_llm_config = lambda: ("http://fake.local", "test-key", "test-model")
        self._orig_sleep = client_mod.time.sleep
        client_mod.time.sleep = lambda s: None
        self.client = LLMClient(provider="deepseek")
        self._orig_urlopen = self.client._request.urlopen

    def tearDown(self):
        settings.get_llm_config = self._orig_cfg
        client_mod.time.sleep = self._orig_sleep
        self.client._request.urlopen = self._orig_urlopen


class TestLLMNetworkRetry(ClientRetryTestBase):
    def test_transient_error_retries_then_succeeds(self):
        """URLError×2 → 第3次成功：chat 正常返回，不抛"""
        import urllib.error
        calls = []

        def flaky(req, timeout=None):
            calls.append(1)
            if len(calls) <= 2:
                raise urllib.error.URLError("瞬时连接故障")
            return FakeResp(_ok_body())

        self.client._request.urlopen = flaky
        out = self.client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(out, "hello")
        self.assertEqual(len(calls), 3)

    def test_persistent_error_raises_after_retries(self):
        """持续故障：重试 3 次后抛 RuntimeError（带已重试标记）"""
        import urllib.error

        def always_fail(req, timeout=None):
            raise urllib.error.URLError("断网")

        self.client._request.urlopen = always_fail
        with self.assertRaisesRegex(RuntimeError, "已重试"):
            self.client.chat([{"role": "user", "content": "hi"}])

    def test_http_4xx_no_retry(self):
        """HTTP 401（Key 无效）：重试无意义，立即抛（不带已重试标记）"""
        import urllib.error
        calls = []

        def unauthorized(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError("u", 401, "Unauthorized", None, None)

        self.client._request.urlopen = unauthorized
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            self.client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(len(calls), 1)


class TestRecognizerRuleFallback(unittest.TestCase):
    def setUp(self):
        self.rec = Recognizer()
        self.rec.client = MagicMock()
        self.rec.client.chat_json.side_effect = RuntimeError("LLM 调用失败(已重试): 断网")

    def test_llm_down_returns_fallback_not_crash(self):
        """识别引擎不可用 → 不抛异常，返回规则回退结构 + degraded 标记"""
        res = self.rec.recognize("我昨天看了一个电影。", level=3)
        self.assertEqual(res["errors"], [])           # 绝不产 confirmed
        self.assertIn("已回退规则匹配", res.get("degraded", ""))
        self.assertEqual(res["kp_total"], len(self.rec.kps))  # 结构完整

    def test_beyond_level_words_become_uncertain_candidates(self):
        """超纲词命中 → 低置信 uncertain 候选（进待确认队列，不进图谱确认层）"""
        import engine.recognizer as rec_mod
        orig = rec_mod.detect_beyond_level
        rec_mod.detect_beyond_level = lambda text, level, lex: [
            {"word": "鞠躬", "level": 5}]
        try:
            res = self.rec.recognize("我给他鞠躬了。", level=3)
        finally:
            rec_mod.detect_beyond_level = orig
        self.assertEqual(len(res["uncertain"]), 1)
        cand = res["uncertain"][0]
        self.assertEqual(cand["fragment"], "鞠躬")
        self.assertEqual(cand["source"], "rule_fallback")
        self.assertLess(cand["confidence"], 0.5)


class TestVerifierFailStreak(unittest.TestCase):
    def setUp(self):
        self.ver = Verifier(client=MagicMock(), graph=None)
        self.kps = [{"id": "kp-1", "text": "要点"}]

    def _verify(self, covered: bool, kp="kp-A"):
        self.ver.client.chat_json_strict.return_value = {
            "point_judgements": [{"point_id": "kp-1", "is_covered": covered}],
            "flag_flowery_but_empty": False}
        bias = {"knowledge_point_id": kp, "fragment": "f", "type": "语法"}
        return self.ver.verify("讲解", self.kps, "复述",
                               bias_ref=bias, commit_graph=False)

    def test_same_task_consecutive_fail_escalates(self):
        """同一任务连续失败：N=2 → retry_simpler；N=4 → suggest_teacher"""
        r1 = self._verify(False)                    # fail 1
        self.assertEqual(r1["consecutive_fail"], 1)
        self.assertIsNone(r1["action"])
        r2 = self._verify(False)                    # fail 2
        self.assertEqual(r2["action"], "retry_simpler")
        r3 = self._verify(False)
        r4 = self._verify(False)                    # fail 4
        self.assertEqual(r4["action"], "suggest_teacher")

    def test_different_tasks_do_not_share_streak(self):
        """回归（4.2 修复）：不同知识点的失败不互相累计（旧实现为实例级计数会串）"""
        r_a = self._verify(False, kp="kp-A")        # A fail 1
        r_b = self._verify(False, kp="kp-B")        # B fail 1（不应该是 2）
        self.assertEqual(r_a["consecutive_fail"], 1)
        self.assertEqual(r_b["consecutive_fail"], 1)
        self.assertIsNone(r_b["action"])            # 旧实现这里会误触发 retry_simpler

    def test_pass_resets_streak(self):
        """pass 清零：fail 后 pass 再 fail，streak 重新从 1 计"""
        self._verify(False)
        rp = self._verify(True)                     # pass → 清零
        self.assertEqual(rp["consecutive_fail"], 0)
        rf = self._verify(False)                    # 重新 fail → 1
        self.assertEqual(rf["consecutive_fail"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
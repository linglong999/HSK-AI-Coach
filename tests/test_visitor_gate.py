# ============================================================
# tests/test_visitor_gate.py
# P0.10 · 游客模式闸门 → B7 S3 · 能量日档位三档拦截
# 覆盖：
#   A. VisitorGate 纯函数：check_start/settle 能量判据、low/exhaust/round_cap 三档、
#      round_cap 单会话 12 回合硬限、换会话归位、energy_limit=0 关闭、跨天重置
#      （mock _utc_today）、只读 get_state 不落盘
#   B. HTTP 集成：首访下发 Set-Cookie、带 cookie 复用同 vid、能量耗尽 429 且模型未被打、
#      单会话超回合 429、BYOK 完全豁免、跨天重置、GET /api/quota 现态
# 对齐验收：每日能量用尽→禁新会话（exhaust）；单会话 ≤12 回合硬限（round_cap 不截断已产出）。
# 运行: python -m unittest tests.test_visitor_gate -v
# ============================================================

import http.client
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.graph.error_graph import ErrorGraph
from engine.quota import report_usage
from engine.serve import create_app
from engine.visitor_gate import (COOKIE_NAME, MSG_ROUND_CAP, ROUND_CAP,
                                 VisitorGate, _utc_today)
from ._serve_common import make_server, stop_server


# ---------------- 替身 ----------------

class FakeRecognizer:
    def recognize(self, text, level=3, native_lang=""):
        return {"errors": [], "uncertain": [], "degraded": []}


class FakeRouter:
    learner_id = "visitor_user"

    def __init__(self):
        self.graph = ErrorGraph("visitor_test")
        self.recognizer = FakeRecognizer()
        self.verifier = None
        self.explainer = None


class CountingLLM:
    """脚本化 llm_call：模拟一个真实产出回合（identify_errors 动作 → text 收尾），
    使对话框产出带 ok 的 trace，能量闸门将其判为"体面产出"入账。
    每回合两条调用，只在 text 收尾时回报一次固定用量（对齐哨兵 ROUND_ENERGY=2.0）。
    统计被调用次数（验证满额时模型未被打）。"""

    def __init__(self, prompt=1000, completion=500):
        self.calls = 0
        self.prompt = prompt
        self.completion = completion

    def __call__(self, messages):
        self.calls += 1
        if self.calls % 2 == 0:
            report_usage({"prompt_tokens": self.prompt,
                          "completion_tokens": self.completion})
            return '[{"type":"text","content":"好的。"}]'
        return '[{"type":"action","name":"identify_errors",' \
               '  "params":{"text":"我想买苹果很多。"}}]'


def _start_server(router, dialog_llm=None, gate=None, tmp=None):
    tmp = tmp or tempfile.mkdtemp()
    if not os.path.isfile(os.path.join(tmp, "index.html")):
        with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
            f.write("<html>visitor</html>")
    app = create_app(router, tmp, dialog_llm=dialog_llm,
                     memory_root=tmp, gate=gate)
    return make_server(app)


def _post(port, path, body, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    c.request("POST", path, json.dumps(body).encode("utf-8"), headers)
    r = c.getresponse()
    raw = r.read()
    set_cookie = r.getheader("Set-Cookie")
    c.close()
    try:
        return r.status, json.loads(raw.decode()), set_cookie
    except Exception:  # noqa: BLE001
        return r.status, None, set_cookie


def _vid_from(set_cookie):
    """从 Set-Cookie 头里取 hsk_vid 值。"""
    if not set_cookie:
        return None
    for part in set_cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE_NAME and v:
            return v
    return None


# ---------------- A · 纯函数 ----------------

class VisitorGateUnitTest(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_low_tier_under_quarter(self):
        # energy_limit=10，用掉 8 → 余 2 < 25%（2.5）→ low
        g = VisitorGate(self.root, energy_limit=10, round_cap=ROUND_CAP)
        g._data["a"] = {"date": _utc_today(), "energy_used": 8.0,
                        "session_id": "s1", "round_count": 1}
        tier, st = g.settle("a", "s1", energy=0.0)   # 0 不叠加、只看现态档位
        self.assertEqual(tier, "low")
        self.assertEqual(st["energy_left"], 2.0)

    def test_exhaust_when_energy_gone(self):
        g = VisitorGate(self.root, energy_limit=10)
        g._data["a"] = {"date": _utc_today(), "energy_used": 10.0,
                        "session_id": "s1", "round_count": 1}
        allowed, tier, _ = g.check_start("a", "s1")
        self.assertFalse(allowed)
        self.assertEqual(tier, "exhaust")

    def test_round_cap_blocks_after_cap(self):
        g = VisitorGate(self.root, energy_limit=100, round_cap=12)
        # 单会话已 12 回合
        g._data["a"] = {"date": _utc_today(), "energy_used": 0.0,
                        "session_id": "s1", "round_count": 12}
        allowed, tier, st = g.check_start("a", "s1")
        self.assertFalse(allowed)
        self.assertEqual(tier, "round_cap")
        self.assertEqual(st["round_count"], 12)

    def test_new_session_resets_round_count(self):
        # 同一 vid 换会话 → 回合计数归位（能量跨会话保留）
        g = VisitorGate(self.root, energy_limit=100, round_cap=12)
        g._data["a"] = {"date": _utc_today(), "energy_used": 5.0,
                        "session_id": "old", "round_count": 12}
        allowed, tier, st = g.check_start("a", "new_session")   # 换会话
        self.assertTrue(allowed)
        self.assertIsNone(tier)
        self.assertEqual(st["round_count"], 0)
        self.assertEqual(st["energy_left"], 95.0)               # 能量不随会话重置

    def test_settle_deducts_and_counts(self):
        g = VisitorGate(self.root, energy_limit=100)
        tier, st = g.settle("a", "s1", energy=2.0)
        self.assertIsNone(tier)                                  # 余量足，无拦截
        self.assertEqual(st["energy_left"], 98.0)
        self.assertEqual(st["round_count"], 1)
        self.assertEqual(st["est_cost"], 2.0)
        # 落盘
        g2 = VisitorGate(self.root, energy_limit=100)
        self.assertEqual(g2._data["a"]["energy_used"], 2.0)

    def test_round_cap_emitted_on_cap_th_round(self):
        g = VisitorGate(self.root, energy_limit=100, round_cap=3)
        for _ in range(3):
            tier, _ = g.settle("a", "s1", energy=1.0)
        # 第 3 回合结束 → round_count=3 == cap → 收尾档
        self.assertEqual(tier, "round_cap")

    def test_zero_disables(self):
        g = VisitorGate(self.root, energy_limit=0)
        allowed, tier, st = g.check_start("a", "s1")
        self.assertTrue(allowed), self.assertIsNone(tier)
        self.assertEqual(st["energy_left"], -1)
        tier2, st2 = g.settle("a", "s1", energy=99.0)
        self.assertIsNone(tier2), self.assertEqual(st2["round_count"], 0)

    def test_cross_day_reset(self):
        g = VisitorGate(self.root, energy_limit=10)
        g.settle("a", "s1", energy=9.0)
        tier, st = g.settle("a", "s1", energy=9.0)   # 同日已用 18 > 10 → 结算后仍显示耗尽
        with mock.patch("engine.visitor_gate._utc_today",
                        return_value="2099-01-02"):
            allowed, t2, st2 = g.check_start("a", "s1")   # 跨天重置能量
            self.assertTrue(allowed)
            self.assertEqual(st2["energy_left"], 10.0)

    def test_get_state_read_only(self):
        g = VisitorGate(self.root, energy_limit=10)
        g.settle("a", "s1", energy=3.0)
        before = dict(g._data.get("a", {}))
        st = g.get_state("a", "s1")
        self.assertEqual(st["energy_left"], 7.0)
        self.assertEqual(st["round_count"], 1)
        self.assertIn("重置", st["reset_at"])
        self.assertEqual(g._data.get("a", {}), before)   # 不落盘

    def test_utc_today_format(self):
        self.assertEqual(len(_utc_today()), 10)      # YYYY-MM-DD
        self.assertEqual(_utc_today()[4], "-")


# ---------------- B · HTTP 集成 ----------------

class VisitorGateHTTPTest(unittest.TestCase):

    # 每回合 mock 用量 1000 in + 500 out → energy=1*1 + 0.5*2 = 2.0
    ROUND_ENERGY = 2.0

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.router = FakeRouter()
        self.llm = CountingLLM()
        self.gate = VisitorGate(self.tmp, energy_limit=4.0)   # 2 回合耗尽
        self.server, self.thr, self.port = _start_server(
            self.router, dialog_llm=self.llm, gate=self.gate, tmp=self.tmp)

    def tearDown(self):
        stop_server(self.server, self.thr)

    def _dialog(self, provider="", cookie=None, conversation="c1"):
        body = {"text": "你好", "learner_id": "u1", "conversation_id": conversation}
        if provider:
            body["provider_id"] = provider
        return _post(self.port, "/api/dialog", body, cookie)

    def test_first_visit_sets_cookie_and_settles_energy(self):
        st, out, sc = self._dialog()
        self.assertEqual(st, 200)
        vid = _vid_from(sc)
        self.assertIsNotNone(vid)                      # 首次下发 vid
        e = self.gate._data[vid]
        self.assertEqual(e["round_count"], 1)
        self.assertAlmostEqual(e["energy_used"], self.ROUND_ENERGY, places=3)

    def test_cookie_reuses_same_vid(self):
        st1, _, sc1 = self._dialog(conversation="c1")
        vid = _vid_from(sc1)
        st2, _, sc2 = self._dialog(cookie=f"{COOKIE_NAME}={vid}", conversation="c1")
        self.assertEqual(st2, 200)
        self.assertIsNone(_vid_from(sc2))              # 已有 vid 不再下发
        self.assertEqual(self.gate._data[vid]["round_count"], 2)

    def test_energy_exhausted_429_no_model_call(self):
        st1, _, sc = self._dialog()                    # 消耗 2
        vid = _vid_from(sc)
        st2, _, _ = self._dialog(cookie=f"{COOKIE_NAME}={vid}")   # 消耗 4 = 满
        self.assertEqual(st2, 200)
        calls_before = self.llm.calls
        st3, out, _ = self._dialog(cookie=f"{COOKIE_NAME}={vid}")  # 已耗尽 → 429
        self.assertEqual(st3, 429)
        self.assertEqual(out["code"], "visitor_energy_exhausted")
        self.assertEqual(out["intercept"]["tier"], "exhaust")
        self.assertIn("复习队列", out["intercept"]["msg"])
        self.assertEqual(self.llm.calls, calls_before)  # 模型未被调用
        self.assertEqual(self.gate._data[vid]["round_count"], 2)  # 回合未再增

    def test_round_cap_hard_limit_over_http(self):
        # 用低 cap 快速触发：round_cap=2，第 2 回合后第 3 次 → 429 round_cap
        cap_root = tempfile.mkdtemp()
        cap_gate = VisitorGate(cap_root, energy_limit=100, round_cap=2)
        tmp2 = tempfile.mkdtemp()
        with open(os.path.join(tmp2, "index.html"), "w", encoding="utf-8") as f:
            f.write("<html>cap</html>")
        s2, t2, p2 = _start_server(self.router, dialog_llm=self.llm,
                                   gate=cap_gate, tmp=tmp2)
        try:
            st1, _, sc = _post(p2, "/api/dialog",
                               {"text": "你好", "conversation_id": "sess"})
            vid = _vid_from(sc)
            st2, _, _ = _post(p2, "/api/dialog",
                              {"text": "你好", "conversation_id": "sess"},
                              f"{COOKIE_NAME}={vid}")
            st3, out, _ = _post(p2, "/api/dialog",
                                {"text": "你好", "conversation_id": "sess"},
                                f"{COOKIE_NAME}={vid}")
            self.assertEqual(st1, 200)
            self.assertEqual(st2, 200)
            self.assertEqual(st3, 429)
            self.assertEqual(out["code"], "visitor_round_cap")
            self.assertEqual(out["intercept"]["tier"], "round_cap")
            self.assertEqual(out["intercept"]["msg"], MSG_ROUND_CAP)
            # 换会话 → 回合计数归位 → 放行
            st4, _, _ = _post(p2, "/api/dialog",
                              {"text": "你好", "conversation_id": "new_sess"},
                              f"{COOKIE_NAME}={vid}")
            self.assertEqual(st4[0] if isinstance(st4, tuple) else st4, 200)
        finally:
            stop_server(s2, t2)

    def test_owner_env_key_uses_quota(self):
        from engine import providers as prov
        st, _, sc = self._dialog(provider=prov.ENV_PROVIDER_ID)
        self.assertEqual(st, 200)
        vid = _vid_from(sc)
        self.assertEqual(self.gate._data[vid]["round_count"], 1)  # env id 仍计游客配额

    def test_byok_fully_exempt(self):
        # 先用 owner 路径耗尽能量
        st1, _, sc = self._dialog()
        vid = _vid_from(sc)
        self._dialog(cookie=f"{COOKIE_NAME}={vid}")
        # BYOK（provider_id=自定义非 env）→ 完全豁免，即使能量已尽
        st, out, _ = self._dialog(provider="myprov", cookie=f"{COOKIE_NAME}={vid}")
        self.assertEqual(st, 200)
        self.assertEqual(self.gate._data[vid]["round_count"], 2)  # 未额外计游客回合

    def test_cross_day_reset_over_http(self):
        st1, _, sc = self._dialog()
        vid = _vid_from(sc)
        self._dialog(cookie=f"{COOKIE_NAME}={vid}")   # 已耗尽
        with mock.patch("engine.visitor_gate._utc_today", return_value="2099-03-03"):
            st, out, _ = self._dialog(cookie=f"{COOKIE_NAME}={vid}")
            self.assertEqual(st, 200)          # 新一天重置能量，放行
            self.assertEqual(self.gate._data[vid]["round_count"], 1)

    # ---------------- C · GET /api/quota 现态（B7 S1/S3） ----------------

    def _quota(self, cookie=None, provider_id="", session_id=None):
        path = "/api/quota"
        qs = []
        if provider_id:
            qs.append(f"provider_id={provider_id}")
        if session_id:
            qs.append(f"session_id={session_id}")
        if qs:
            path += "?" + "&".join(qs)
        headers = {}
        if cookie:
            headers["Cookie"] = cookie
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path, headers=headers)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, json.loads(raw.decode())

    def test_quota_unlimited_when_byok(self):
        st, out = self._quota(provider_id="myprov")
        self.assertEqual(st, 200)
        self.assertEqual(out["energy_left"], -1)
        self.assertEqual(out["daily_total"], 0)

    def test_quota_reports_gate_state_no_consume(self):
        st1, _, sc = self._dialog(conversation="c1")   # 消耗 2.0
        vid = _vid_from(sc)
        st, out = self._quota(cookie=f"{COOKIE_NAME}={vid}", session_id="c1")
        self.assertEqual(st, 200)
        self.assertEqual(out["energy_left"], 2.0)   # 4.0 - 2.0
        self.assertEqual(out["daily_total"], 4.0)
        self.assertEqual(out["round_count"], 1)
        # GET 只读：不额外消耗回合
        self.assertEqual(self.gate._data[vid]["round_count"], 1)


if __name__ == "__main__":
    unittest.main()
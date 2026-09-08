# -*- coding: utf-8 -*-
"""0.26 隐性"为什么"原理：authoring prompt / 宽容解析 / 生成降级 / serve 入口接线。
对齐铁律：errors 为空 → 不调用；LLM/解析失败 → 空列表（前端无折叠块），不阻断对话；
动态内容 textContent 注入（前端）；l1 字段仅在给定时透传（宁缺勿错）。"""
import unittest
from unittest import mock

from engine.generation.authoring import (
    WHY_CONSTRAINTS, WHY_FIELDS, build_why_prompt)
from engine.generation.why import generate_why, parse_why_items


class _StubClient:
    """可控的 chat_json_strict 替身：返回预设 parsed，或可抛异常。"""
    def __init__(self, parsed=None, exc=None):
        self._parsed = parsed
        self._exc = exc
        self.calls = []

    def chat_json_strict(self, system, user, temperature=0.2, retries=1, config=None):
        self.calls.append({"user": user, "config": config})
        if self._exc:
            raise self._exc
        return self._parsed


class BuildWhyPromptTest(unittest.TestCase):
    def test_empty_errors_returns_empty(self):
        self.assertEqual(build_why_prompt(errors=[]), "")

    def test_prompt_embeds_fragments_and_keeps_chinese(self):
        p = build_why_prompt(
            errors=[{"fragment": "吃奶茶", "correction": "喝奶茶",
                     "type": "词汇", "confidence": 0.8}],
            l1_hypotheses=[{"l1_anchor": "drink", "correction": "喝"}])
        self.assertIn("吃奶茶", p)
        self.assertIn("喝奶茶", p)
        self.assertIn("drink", p)
        # 语言指令可注入
        self.assertIn("语言指令", p)

    def test_no_l1_only_omits_l1_section_not_break(self):
        p = build_why_prompt(errors=[{"fragment": "二杯", "correction": "两杯"}])
        self.assertIn("二杯", p)
        self.assertIn("两杯", p)


class ParseWhyItemsTest(unittest.TestCase):
    def test_valid_items_filtered_to_whitelist(self):
        parsed = {"items": [
            {"fragment": "吃奶茶", "correction": "喝奶茶", "reason": "饮品用喝",
             "l1": "drink", "extra": "忽略"},
            {"fragment": "二杯", "correction": "两杯", "reason": "量词前用两"},
        ]}
        out = parse_why_items(parsed)
        self.assertEqual(len(out), 2)
        self.assertEqual(set(out[0].keys()), set(WHY_FIELDS))  # extra 被剔除
        self.assertNotIn("extra", out[0])

    def test_non_list_and_empty_return_empty(self):
        self.assertEqual(parse_why_items("nope"), [])
        self.assertEqual(parse_why_items(None), [])
        self.assertEqual(parse_why_items({"items": []}), [])

    def test_bad_rows_skipped(self):
        parsed = {"items": [{"reason": "ok", "correction": "c"}, "bad",
                            {"fragment": "f"}]}
        out = parse_why_items(parsed)
        # 第二条非 dict 被跳过；第三条无 reason/fragment 无意义也跳过
        self.assertEqual(len(out), 1)

    def test_l1_omitted_when_absent(self):
        out = parse_why_items({"items": [
            {"fragment": "a", "correction": "b", "reason": "r"}]})
        self.assertEqual(len(out), 1)
        self.assertNotIn("l1", out[0])


class GenerateWhyTest(unittest.TestCase):
    def test_empty_errors_returns_empty_no_call(self):
        c = _StubClient(parsed={"items": [{"reason": "x"}]})
        self.assertEqual(generate_why(client=c, errors=[]), [])
        self.assertEqual(c.calls, [])

    def test_success_returns_normalized(self):
        c = _StubClient(parsed={"items": [
            {"fragment": "吃奶茶", "correction": "喝奶茶", "reason": "饮品用喝"}]})
        out = generate_why(client=c, errors=[{"fragment": "吃奶茶",
                                              "correction": "喝奶茶"}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["reason"], "饮品用喝")
        self.assertIn("config", c.calls[0])

    def test_config_passthrough_to_client(self):
        c = _StubClient(parsed={"items": [{"reason": "r"}]})
        cfg = {"base_url": "u", "api_key": "k", "model": "m"}
        generate_why(client=c, errors=[{"fragment": "a", "correction": "b"}],
                     config=cfg)
        self.assertEqual(c.calls[0]["config"], cfg)

    def test_llm_failure_falls_back_to_empty(self):
        c = _StubClient(exc=RuntimeError("boom"))
        self.assertEqual(generate_why(client=c, max_repairs=1,
                                      errors=[{"fragment": "a"}]),
                         [])

    def test_invalid_shape_falls_back_to_empty(self):
        c = _StubClient(parsed={"not_items": True})
        self.assertEqual(generate_why(
            client=c, max_repairs=1, errors=[{"fragment": "a"}]), [])

    def test_default_client_used_when_none(self):
        # 不传 client → 内部 LLMClient()；此处用 mock 确认路径被触发即返回空
        # 注意 patch 目标必须是 why 模块内的名字绑定（why.py 是
        # from engine.llm.client import LLMClient），非源模块属性——
        # 否则模块缓存时序不同会导致 patch 失效（CI 3.11/Ubuntu 曾间歇红）。
        with mock.patch("engine.generation.why.LLMClient") as MC:
            MC.return_value.chat_json_strict.return_value = {
                "items": [{"fragment": "a", "correction": "b", "reason": "r"}]}
            out = generate_why(errors=[{"fragment": "a", "correction": "b"}],
                               max_repairs=0)
        self.assertEqual(len(out), 1)


class ServeBuildWhyTest(unittest.TestCase):
    """serve 入口 _build_why：gate + provider 配置注入 + 异常兜底。"""

    @staticmethod
    def _handler():
        from engine.serve import make_handler
        H = make_handler(_DUMMY_ROUTER, _FAKE_INDEX)
        return H

    def setUp(self):
        # 打桩 lazy import 路径，让 _build_why 不触发真实网络
        patcher1 = mock.patch("engine.generation.why.generate_why")
        patcher2 = mock.patch("engine.llm.client.LLMClient")
        self.p1 = patcher1.start()
        self.p2 = patcher2.start()
        self.addCleanup(self.p1.stop)
        self.addCleanup(self.p2.stop)
        self.H = ServeBuildWhyTest._handler()

    def test_none_pre_scan_returns_empty(self):
        self.assertEqual(self.H._build_why(None, {"id": "x"}, "zh"), [])
        self.assertEqual(self.H._build_why({"errors": []}, {"id": "x"}, "zh"),
                         [])
        self.p1.assert_not_called()

    def test_errors_forwarded_with_provider_config(self):
        self.p1.return_value = [{"fragment": "a", "reason": "r"}]
        prov = {"id": "d", "base_url": "http://u", "api_key": "k", "model": "m"}
        out = self.H._build_why({"errors": [{"fragment": "a"}],
                                 "hypotheses": [{"l1_anchor": "h"}],
                                 }, prov, "en")
        self.assertEqual(out[0]["reason"], "r")
        args, kwargs = self.p1.call_args
        self.assertEqual(kwargs["config"]["api_key"], "k")
        self.assertIn("English", kwargs["language_directive"])

    def test_zh_language_directive(self):
        self.p1.return_value = []
        self.H._build_why({"errors": [{"fragment": "a"}]}, None, "zh")
        self.assertIn("中文", self.p1.call_args.kwargs["language_directive"])
        self.assertIsNone(self.p1.call_args.kwargs["config"])

    def test_generate_failure_degrades_to_empty(self):
        self.p1.side_effect = RuntimeError("boom")
        self.assertEqual(self.H._build_why({"errors": [{"fragment": "a"}]},
                                           {"id": "x"}, "zh"), [])


class CardPersistenceTest(unittest.TestCase):
    """0.27 成果卡随会话持久：_compact_cards 过滤 + _assistant_payload gate/门槛。"""

    @classmethod
    def setUpClass(cls):
        from engine.serve import make_handler
        cls.H = make_handler(_DUMMY_ROUTER, __file__)

    def test_compact_cards_filters_and_keeps_result(self):
        trace = [
            # 白名单卡：保留 name+result
            {"name": "identify_errors", "ok": True,
             "result": {"errors": [{"fragment": "a"}]}},
            # ok=False 的丢卡
            {"name": "explain_error", "ok": False, "result": {}},
            # 未知技能：不渲染
            {"name": "secret_tool", "ok": True, "result": {"x": 1}},
            # 非 dict：忽略
            "junk",
        ]
        out = self.H._compact_cards(trace)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "identify_errors")
        self.assertIn("result", out[0])
        self.assertNotIn("params", out[0])   # 只留 {name, result}
        self.assertNotIn("ok", out[0])

    def test_compact_cards_empty_and_none(self):
        self.assertEqual(self.H._compact_cards(None), [])
        self.assertEqual(self.H._compact_cards([]), [])

    def test_assistant_payload_no_provider_skips_why(self):
        res = {"trace": [{"name": "identify_errors", "ok": True,
                          "result": {"errors": [{"fragment": "a"}]}}]}
        with mock.patch.object(self.H, "_build_why") as bw:
            payload = self.H._assistant_payload(
                res, {"errors": [{"fragment": "a"}]}, None, "zh", "fluent")
        bw.assert_not_called()                # 无 provider → 不触网
        self.assertEqual(payload["why"], [])
        self.assertEqual(len(payload["cards"]), 1)

    def test_assistant_payload_provider_generates_why(self):
        res = {"trace": [{"name": "identify_errors", "ok": True,
                          "result": {"errors": [{"fragment": "a"}]}}]}
        with mock.patch.object(self.H, "_build_why",
                               return_value=[{"fragment": "a", "reason": "r"}]) as bw:
            payload = self.H._assistant_payload(
                res, {"errors": [{"fragment": "a"}]},
                {"id": "d"}, "zh", "fluent")
        bw.assert_called_once()
        self.assertEqual(payload["why"][0]["reason"], "r")

    def test_assistant_payload_material_suppresses_why(self):
        res = {"trace": [], "text": ""}
        with mock.patch.object(self.H, "_build_why") as bw:
            payload = self.H._assistant_payload(
                res, {"errors": [{"fragment": "a"}]},
                {"id": "d"}, "zh", "material")
        bw.assert_not_called()
        self.assertEqual(payload["why"], [])

    def test_assistant_payload_no_errors_suppresses_why(self):
        res = {"trace": [], "text": ""}
        with mock.patch.object(self.H, "_build_why") as bw:
            payload = self.H._assistant_payload(
                res, {"errors": []}, {"id": "d"}, "zh", "fluent")
        bw.assert_not_called()
        self.assertEqual(payload["why"], [])


class AttachKpToWhyTest(unittest.TestCase):
    """0.29 为什么错误行分支动作：_attach_kp_to_why 确定性回配 kp_id。"""
    _H = None

    @classmethod
    def setUpClass(cls):
        from engine.serve import make_handler
        cls._H = make_handler(_DUMMY_ROUTER, __file__)

    def test_exact_fragment_match_adds_kp(self):
        items = [{"fragment": "吃奶茶", "correction": "喝奶茶", "reason": "r"}]
        errors = [{"fragment": "吃奶茶", "correction": "喝奶茶", "type": "词汇",
                   "confidence": 0.9, "knowledge_point_id": "kp-drink"}]
        out = self._H._attach_kp_to_why(items, errors)
        self.assertEqual(out[0]["kp_id"], "kp-drink")
        self.assertEqual(out[0]["type"], "词汇")
        self.assertEqual(out[0]["confidence"], 0.9)
        self.assertEqual(out[0]["reason"], "r")  # 原字段保留

    def test_fallback_correction_match(self):
        # fragment 被 LLM 改写 → 用 correction 兜底映射
        items = [{"fragment": "吃那杯奶茶", "correction": "喝奶茶", "reason": "r"}]
        errors = [{"fragment": "吃奶茶", "correction": "喝奶茶",
                   "knowledge_point_id": "kp-drink"}]
        out = self._H._attach_kp_to_why(items, errors)
        self.assertEqual(out[0]["kp_id"], "kp-drink")

    def test_index_fallback_when_no_text_match(self):
        items = [{"fragment": "改写的片段", "correction": "改了", "reason": "r"}]
        errors = [{"fragment": "吃奶茶", "correction": "喝奶茶",
                   "knowledge_point_id": "kp-drink"}]
        out = self._H._attach_kp_to_why(items, errors)
        self.assertEqual(out[0]["kp_id"], "kp-drink")  # 索引位兜底

    def test_no_match_out_of_range_no_fabrication(self):
        items = [{"fragment": "孤行无著", "correction": "x", "reason": "r"}]
        out = self._H._attach_kp_to_why(items, [])     # errors 为空且无索引位
        self.assertNotIn("kp_id", out[0])              # 不臆造节点
        self.assertEqual(out[0]["fragment"], "孤行无著")

    def test_kp_from_graph_write_when_top_level_missing(self):
        items = [{"fragment": "二杯", "correction": "两杯", "reason": "r"}]
        errors = [{"fragment": "二杯", "graph_write": {"kp_id": "kp-two"}}]
        out = self._H._attach_kp_to_why(items, errors)
        self.assertEqual(out[0]["kp_id"], "kp-two")

    def test_empty_items_passthrough(self):
        self.assertEqual(self._H._attach_kp_to_why([], [{"fragment": "a"}]), [])
        self.assertEqual(self._H._attach_kp_to_why(None, [{"fragment": "a"}]), None)

    def test_parse_why_items_strips_kp_but_serve_reattaches(self):
        # 0.29 边界：generate_why 的判白名单会剔除 kp_id，
        # 但 serve 的 _attach_kp_to_why 是事后回配，二者叠加才有完整 kp_id
        parsed = {"items": [{"fragment": "二杯", "correction": "两杯",
                             "reason": "r", "kp_id": "kp-two"}]}
        items = parse_why_items(parsed)
        self.assertNotIn("kp_id", items[0])           # 白名单剔除
        out = self._H._attach_kp_to_why(
            items, [{"fragment": "二杯", "correction": "两杯",
                     "knowledge_point_id": "kp-two"}])
        self.assertEqual(out[0]["kp_id"], "kp-two")   # 事后回配补上


# 供 _handler 构造的占位对象（_build_why 不触碰 router 字段）
class _DUMMY:  # noqa: N801
    pass


_DUMMY_ROUTER = _DUMMY()
_FAKE_INDEX = __file__  # make_handler 只存不校验

if __name__ == "__main__":
    unittest.main()
# -*- coding: utf-8 -*-
# tests/test_llm_injection.py —— 03 LLM 注入缝横扫：file_mode + dialog_service._build_why
# 验证补缝后：注入的假 client 被使用；默认构造不被破坏（零行为改动）。
# 全程 mock，不触网、不花钱。

import json
import unittest
from unittest import mock

from engine.dialog_service import DialogService
from engine.file_mode import FileCoach


class FakeLLM:
    """极简 duck-typed LLM 客户端：记录收到什么，返回可控回复。"""

    def __init__(self, reply="OK"):
        self.reply = reply
        self.calls = []

    def chat(self, *a, **k):
        self.calls.append((a, k))
        return self.reply

    def chat_json_strict(self, system, user, **k):
        self.calls.append((system, user, k))
        # 返回合法的 why-items 结构（{items:[{fragment,correction,reason}]}）
        return {"items": [{"fragment": "很多苹果", "correction": "苹果很多",
                          "reason": "定语后置"}]}

    def chat_stream(self, *a, **k):
        self.calls.append((a, k))
        yield {"delta": self.reply}


class FileModeInjectionTest(unittest.TestCase):
    def test_client_injected_used(self):
        fake = FakeLLM()
        fc = FileCoach(client=fake)
        self.assertIs(fc._client, fake)

    def test_default_client_is_real_llm(self):
        fc = FileCoach()
        # 默认构造 LLMClient（非注入对象）——证明默认路径未被破坏。
        # 用类型名判断：既避免依赖模块级 mock 泄漏，也验证是真实客户端而非注入对象
        from engine.llm.client import LLMClient
        self.assertEqual(type(fc._client).__name__, LLMClient.__name__)


class BuildWhyInjectionTest(unittest.TestCase):
    def _pre_scan(self):
        return {
            "errors": [{"fragment": "很多苹果", "fix": "苹果很多", "type": "语序",
                        "kp_candidate": {"id": "kp-a", "text": "定语后置"}}],
            "hypotheses": [],
        }

    def test_client_injected_forwarded_to_generate_why(self):
        # 用独立假 generate_why 记录"收到哪个 client"，避免与 test_why 共享 mock 目标。
        fake = FakeLLM()
        captured = {}

        def spy_generate_why(client, errors, hypotheses, **kw):
            captured["client"] = client
            captured["config"] = kw.get("config")
            return [{"fragment": "很多苹果", "correction": "苹果很多", "reason": "r"}]

        # _build_why 内 `from engine.generation.why import generate_why`（函数内 import
        # 每次调用读取模块属性）→ patch engine.generation.why.generate_why 生效。
        with mock.patch("engine.generation.why.generate_why", spy_generate_why):
            provider = {"base_url": "http://x", "api_key": "k", "model": "m"}
            with mock.patch("engine.dialog_service.DialogService._attach_kp_to_why",
                            side_effect=lambda items, errs: items):
                out = DialogService._build_why(
                    self._pre_scan(), provider, "en", client=fake)
        # 注入 client 原样转发给 generate_why（核心：client 参数不被丢弃）
        self.assertIs(captured["client"], fake)
        # provider config 仍正确传递（BYOK 语义未被补缝破坏）
        self.assertEqual(captured["config"]["api_key"], "k")
        self.assertEqual(out[0]["reason"], "r")

    def test_no_errors_returns_empty_without_client(self):
        fake = FakeLLM()
        with mock.patch("engine.dialog_service.DialogService._attach_kp_to_why") as att:
            out = DialogService._build_why({}, None, "en", client=fake)
        self.assertEqual(out, [])
        att.assert_not_called()
        self.assertFalse(fake.calls)


if __name__ == "__main__":
    unittest.main()
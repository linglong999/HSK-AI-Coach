# -*- coding: utf-8 -*-
# tests/test_provider_resolver.py —— ProviderResolver 注入缝单测（深化批次C）
# 覆盖：注入路径跳过解析并恒绑注入值 / unknown_provider 400 / llm_not_configured 400
# （无供应商、缺 api_key 两形态）/ 命中解析 / bind_llm_call 三分支（注入值、
# provider 绑定配置透传、None→默认实现）。providers 存储与 LLMClient 全 mock，
# 零网络、零文件依赖。

import unittest
from unittest import mock

from engine.dialog_service import ProviderResolver, _default_dialog_llm


def _provider(pid="p1", key="sk-x"):
    return {"id": pid, "name": "P1", "model": "m",
            "base_url": "http://b", "api_key": key}


def _injected_llm(messages):
    return "injected"


class InjectedPathTest(unittest.TestCase):
    def test_injected_skips_resolve_and_binds_injected(self):
        r = ProviderResolver("data", injected_llm=_injected_llm)
        self.assertTrue(r.injected)
        # 解析恒跳过（任意 provider_id → (None, None)，不触 providers 存储）
        self.assertEqual(r.resolve("whatever"), (None, None))
        self.assertIs(r.bind_llm_call(_provider()), _injected_llm)
        self.assertIs(r.bind_llm_call(None), _injected_llm)


class RealPathResolveTest(unittest.TestCase):
    def setUp(self):
        self.r = ProviderResolver("data")
        self.assertFalse(self.r.injected)

    def test_unknown_provider_400(self):
        with mock.patch("engine.providers.effective_providers",
                        return_value=[_provider()]) as eff:
            provider, err = self.r.resolve("gone")
        self.assertIsNone(provider)
        self.assertEqual(err[0], 400)
        self.assertEqual(err[1]["code"], "unknown_provider")
        self.assertIn("重新选择", err[1]["error"])
        eff.assert_called_once_with("data")

    def test_known_provider_resolved(self):
        with mock.patch("engine.providers.effective_providers",
                        return_value=[_provider()]):
            provider, err = self.r.resolve("p1")
        self.assertIsNone(err)
        self.assertEqual(provider["id"], "p1")

    def test_no_provider_configured_400(self):
        with mock.patch("engine.providers.resolve_provider",
                        return_value=None) as rp:
            provider, err = self.r.resolve("")
        self.assertIsNone(provider)
        self.assertEqual(err[0], 400)
        self.assertEqual(err[1]["code"], "llm_not_configured")
        rp.assert_called_once_with("data", None)

    def test_provider_without_key_400(self):
        with mock.patch("engine.providers.resolve_provider",
                        return_value=_provider(key="")):
            provider, err = self.r.resolve("")
        self.assertIsNone(provider)
        self.assertEqual(err[1]["code"], "llm_not_configured")


class BindLlmCallTest(unittest.TestCase):
    def test_none_binds_default(self):
        r = ProviderResolver("data")
        self.assertIs(r.bind_llm_call(None), _default_dialog_llm)

    def test_provider_binds_configured_client(self):
        r = ProviderResolver("data")
        captured = {}

        class FakeClient:
            def __init__(self):
                captured["client"] = self

            def chat(self, messages, temperature=None, config=None):
                captured.update(messages=messages, temperature=temperature,
                                config=config)
                return "resp"

        with mock.patch("engine.llm.client.LLMClient", FakeClient):
            llm = r.bind_llm_call(_provider())
        self.assertEqual(llm([{"role": "user", "content": "hi"}]), "resp")
        self.assertEqual(captured["temperature"], 0.3)
        self.assertEqual(captured["config"]["base_url"], "http://b")
        self.assertEqual(captured["config"]["api_key"], "sk-x")
        self.assertEqual(captured["config"]["model"], "m")


class DialogServiceWiringTest(unittest.TestCase):
    """DialogService 构造面：dialog_llm 包装为注入式 resolver；
    显式 provider_resolver 注入优先（批次C 注入路径统一）。"""

    class _FakeRouter:
        learner_id = "w"
        native_lang = "zh"

        class graph:
            @staticmethod
            def graph_snapshot():
                return {"nodes": []}

    def test_dialog_llm_wrapped_as_injected_resolver(self):
        from engine.dialog_service import DialogService
        svc = DialogService(router=self._FakeRouter(), dialog_llm=_injected_llm)
        self.assertTrue(svc._resolver.injected)
        self.assertIs(svc._resolver.bind_llm_call(None), _injected_llm)

    def test_explicit_resolver_takes_precedence(self):
        from engine.dialog_service import DialogService
        explicit = ProviderResolver("data", injected_llm=_injected_llm)
        svc = DialogService(router=self._FakeRouter(), dialog_llm=None,
                            provider_resolver=explicit)
        self.assertIs(svc._resolver, explicit)


if __name__ == "__main__":
    unittest.main()

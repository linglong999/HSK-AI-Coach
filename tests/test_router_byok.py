# -*- coding: utf-8 -*-
# tests/test_router_byok.py —— 04 Router 请求上下文传播 + BYOK 顺路并进 Router
# 验证：① 三引擎把 config 透传给 client 调用；② Router.process 经 RequestContext
# 把 provider_config / native_lang / level 传进识别与讲解；③ verify_rephrase 同样透传；
# ④ DialogService._request_ctx 宽松解析 provider → RequestContext（注入路径恒 None）。
# 全程 mock，不触网、不花钱。

import os
import tempfile
import unittest

from engine.recognizer import Recognizer
from engine.explainer import Explainer
from engine.verifier import Verifier
from engine.router import Router, RequestContext
from engine.dialog_service import DialogService, ProviderResolver


class FakeLLM:
    """duck-typed LLM 客户替身：记录每次调用收到的 config。"""

    def __init__(self, recognize_errors=None):
        self.calls = []
        self.recognize_errors = recognize_errors or []

    def chat_json(self, system, user, temperature=0.2, config=None):
        self.calls.append(("recognize", config))
        return {"errors": self.recognize_errors, "uncertain": []}

    def chat_json_strict(self, system, user, temperature=0.2, retries=1, config=None):
        self.calls.append(("strict", config))
        return {"key_points": [], "explanation": "x", "keywords": []}


FAKE_CONFIRMED = {
    "fragment": "苹果很多", "correction": "很多苹果", "type": "语法",
    "type_confident": True, "confidence": 0.9, "knowledge_point_id": "",
}
CFG = {"base_url": "http://byok", "api_key": "k", "model": "m"}


class EngineConfigPassthroughTest(unittest.TestCase):
    """① 三引擎把 config 原样透传给 client 调用（None → 不传也算透传）。"""

    def test_recognizer_passes_config(self):
        fake = FakeLLM()
        r = Recognizer(client=fake)
        r.recognize("我想买苹果很多。", config=CFG)
        self.assertEqual(fake.calls[0][1], CFG)

    def test_explainer_passes_config(self):
        fake = FakeLLM()
        e = Explainer(client=fake)
        e.explain({"sentence": "很多苹果", "fragment": "苹果很多",
                   "correction": "很多苹果", "type": "语法"}, config=CFG)
        self.assertEqual(fake.calls[0][1], CFG)

    def test_verifier_passes_config(self):
        fake = FakeLLM()
        v = Verifier(client=fake, graph=None)
        v.verify("讲解", [{"id": "1", "text": "语序"}], "很多苹果",
                 commit_graph=False, config=CFG)
        self.assertEqual(fake.calls[0][1], CFG)


class RouterCtxTest(unittest.TestCase):
    """②③ Router 把请求上下文（provider_config / native_lang / level）传入引擎。"""

    LEARNER = "test_byok"

    def setUp(self):
        self.router = Router(learner_id=self.LEARNER, native_lang="英语",
                             user_level="HSK3")
        # 注入假 client 到三引擎，拦截 config
        self.rec_client = FakeLLM(recognize_errors=[dict(FAKE_CONFIRMED)])
        self.exp_client = FakeLLM()
        self.ver_client = FakeLLM()
        self.router.recognizer.client = self.rec_client
        self.router.explainer.client = self.exp_client
        self.router.verifier.client = self.ver_client
        self.data_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data", f"graph_{self.LEARNER}.json")

    def tearDown(self):
        if os.path.exists(self.data_path):
            os.remove(self.data_path)

    def test_process_provider_config_forwarded_to_recognizer_and_explainer(self):
        res = self.router.process("我想买苹果很多。", event_key="e1",
                                  ctx=RequestContext(provider_config=CFG))
        self.assertEqual(self.rec_client.calls[0][1], CFG)   # 识别收到 BYOK config
        self.assertEqual(self.exp_client.calls[0][1], CFG)   # 讲解收到 BYOK config
        self.assertTrue(res["errors"])                       # 链路照常出偏误

    def test_request_ctx_overrides_native_and_level(self):
        res = self.router.process("我想买苹果很多。", event_key="e2",
                                  ctx=RequestContext(native_lang="ko",
                                                     user_level="HSK2"))
        self.assertEqual(res["native_lang"], "ko")   # 响应回显请求级覆盖
        self.assertEqual(res["user_level"], "HSK2")
        # 请求级覆盖不污染会话级字段（图/会话仍按实例默认）
        self.assertEqual(self.router.native_lang, "英语")
        self.assertEqual(self.router.user_level, "HSK3")

    def test_without_ctx_falls_back_to_instance(self):
        res = self.router.process("我想买苹果很多。", event_key="e3")
        self.assertEqual(res["native_lang"], "英语")
        self.assertEqual(res["user_level"], "HSK3")
        self.assertIsNone(self.rec_client.calls[0][1])   # 无 ctx → config 传 None

    def test_verify_rephrase_forwards_config(self):
        cfg = {"base_url": "http://v", "api_key": "k", "model": "m"}
        self.router.verify_rephrase("讲解", [{"id": "1", "text": "语序"}], "很多苹果",
                                    event_key="v1", ctx=RequestContext(provider_config=cfg))
        self.assertEqual(self.ver_client.calls[0][1], cfg)


class RequestCtxTest(unittest.TestCase):
    """④ DialogService._request_ctx：宽松解析 provider → RequestContext。"""

    def _svc(self, injected_llm=None):
        root = tempfile.mkdtemp()
        return DialogService(router=object(), memory_root=root,
                             provider_resolver=ProviderResolver(root, injected_llm=injected_llm))

    def test_mock_path_returns_none(self):
        svc = self._svc(injected_llm=lambda m: "x")
        self.assertIsNone(svc._request_ctx({"provider_id": "p-1"}))

    def test_no_provider_returns_none(self):
        from unittest import mock
        from engine import providers
        svc = self._svc()
        # 显式 patch：无任何可用供应商（即使本机 .env 已配 Key，也不解析出 ctx）
        with mock.patch.object(providers, "resolve_provider", return_value=None):
            self.assertIsNone(svc._request_ctx({}))

    def test_known_provider_builds_ctx(self):
        from engine import providers
        root = tempfile.mkdtemp()
        providers.save_store(root, {"providers": [
            {"id": "p-1", "name": "x", "base_url": "http://u", "api_key": "k",
             "model": "m", "source": "ui"}], "default_id": "p-1"})
        svc = DialogService(router=object(), memory_root=root,
                            provider_resolver=ProviderResolver(root))
        ctx = svc._request_ctx({})
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx.provider_config["api_key"], "k")


if __name__ == "__main__":
    unittest.main()
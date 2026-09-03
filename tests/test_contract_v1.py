# ============================================================
# JSON 接缝契约 v1 对齐测试
# 验证 Router.process() 输出严格满足 datasets/docs/JSON-接缝契约-v1.md：
#  - 纯可序列化（json.dumps 直接可用，无 dataclass/对象）
#  - contract_version / meta / degraded 结构化 / error 字段白名单稳定序
# 全部 mock 引擎层，不调用 LLM、不需要 API Key。
# 运行: python -m unittest tests.test_contract_v1 -v
# ============================================================

import json
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.router import Router

FAKE_CONFIRMED = {
    "fragment": "苹果很多",
    "correction": "很多苹果",
    "type": "语法",
    "type_confident": True,
    "confidence": 0.9,
    "knowledge_point_id": "kp-test-yuxu",
}
FAKE_UNCERTAIN = {
    "fragment": "很",
    "correction": "",
    "type": "语用",
    "type_confident": False,
    "confidence": 0.4,
    "knowledge_point_id": "",
}

TRUE_KEYS = {"contract_version", "learner_id", "user_level", "native_lang",
             "input_text", "errors", "uncertain", "hypotheses", "has_error",
             "graph_size", "review_queue", "degraded", "meta"}
META_KEYS = {"start_ts", "end_ts", "elapsed_ms"}


def _mock_ok(router):
    router.recognizer.recognize = lambda text, native_lang="": {
        "errors": [dict(FAKE_CONFIRMED)], "uncertain": [dict(FAKE_UNCERTAIN)],
        "hypotheses": [], "degraded": []}
    router.explainer.explain = lambda err, **kw: {
        "explanation": "应把数量短语放名词前。例：很多水。",
        "key_points": [{"id": "kp-1", "text": "很多+名词语序"}],
        "keywords": ["语序"], "uncertain_note": "", "free_generated": False}


class ContractBase(unittest.TestCase):
    LEARNER = "test_contract"
    DATA = os.path.join(_PROJECT_ROOT, "data", f"graph_test_contract.json")

    def setUp(self):
        self.router = Router(learner_id=self.LEARNER, native_lang="英语",
                             user_level="HSK3")
        _mock_ok(self.router)

    def tearDown(self):
        if os.path.exists(self.DATA):
            os.remove(self.DATA)


class TestContractShape(ContractBase):

    def test_top_level_keys_exact(self):
        """顶层字段严格等于契约 v1（不多不少）"""
        res = self.router.process("我想买苹果很多。", event_key="c1")
        self.assertEqual(set(res.keys()), TRUE_KEYS)
        # 为空时 errors 仍为 list
        self.assertIsInstance(res["errors"], list)
        self.assertIsInstance(res["uncertain"], list)

    def test_meta_shape(self):
        res = self.router.process("我想买苹果很多。", event_key="c2")
        self.assertEqual(set(res["meta"].keys()), META_KEYS)
        self.assertIsInstance(res["meta"]["elapsed_ms"], int)
        self.assertTrue(bool(res["meta"]["end_ts"]))

    def test_plain_serializable(self):
        """前端直接 json.dumps 不应抛异常 —— 无 dataclass/对象残留"""
        res = self.router.process("我想买苹果很多。", event_key="c3")
        json.dumps(res, ensure_ascii=False)  # 应静默成功

    def test_error_field_whitelist_order(self):
        """error 字段严格白名单 + 稳定顺序（前端不依赖内部实现字段）"""
        res = self.router.process("我想买苹果很多。", event_key="c4")
        err = res["errors"][0]["error"]
        self.assertEqual(list(err.keys()),
                         ["fragment", "correction", "type", "type_confident",
                          "confidence", "knowledge_point_id", "uncertain"])
        self.assertIsInstance(err["confidence"], float)
        self.assertIsInstance(err["type_confident"], bool)

    def test_uncertain_same_schema(self):
        """uncertain 条目与 error 同构（契约 §1）"""
        res = self.router.process("我想买苹果很多。", event_key="c5")
        u = res["uncertain"][0]
        self.assertEqual(list(u.keys()),
                         ["fragment", "correction", "type", "type_confident",
                          "confidence", "knowledge_point_id", "uncertain"])
        self.assertIsInstance(u["uncertain"], bool)
        # uncertain 走契约 §1 白名单（键顺序稳定）即可，值随识别通道决定

    def test_explanation_degraded_flag_absent_on_ok(self):
        """正常讲解：无 _degraded 标志，含契约字段"""
        res = self.router.process("我想买苹果很多。", event_key="c6")
        expl = res["errors"][0]["explanation"]
        self.assertNotIn("_degraded", expl)
        for k in ("explanation", "key_points", "keywords",
                  "uncertain_note", "free_generated"):
            self.assertIn(k, expl)
        self.assertIsInstance(expl["key_points"], list)

    def test_has_error_and_queue(self):
        res = self.router.process("我想买苹果很多。", event_key="c7")
        self.assertIs(True, res["has_error"])
        self.assertGreaterEqual(res["graph_size"], 1)
        self.assertIsInstance(res["review_queue"], list)

    def test_degraded_empty_success_path(self):
        res = self.router.process("我想买苹果很多。", event_key="c8")
        self.assertEqual(res["degraded"], [])
        # 检查内部 Graph 无 LLM 调用（契约不加 side 字段）
        self.assertEqual("v1", res["contract_version"])

    def test_rule_fallback_degraded_passthrough(self):
        """识别层降级（规则回退，degraded 为字符串）必须透传进契约 degraded[]（§5）。
        回归防护：曾因 isinstance(list) 检查把字符串静默丢弃。"""
        fallback = {
            "errors": [], "uncertain": [dict(FAKE_UNCERTAIN, uncertain=True)],
            "raw": {}, "kp_total": 0, "beyond_level": [], "beyond_level_flag": False,
            "dropped_fp": [],
            "degraded": "识别引擎不可用，已回退规则匹配（仅超纲词预检）: LLM down",
        }
        self.router.recognizer.recognize = lambda text, native_lang="": fallback
        res = self.router.process("我想买苹果很多。", event_key="c9")
        notices = [d for d in res["degraded"] if d.get("stage") == "recognize"]
        self.assertEqual(len(notices), 1)
        self.assertIn("已回退规则匹配", notices[0]["reason"])
        self.assertIs(False, notices[0]["fatal"])  # 非致命：uncertain 候选仍在，链条继续
        self.assertEqual(len(res["uncertain"]), 1)
        self.assertIs(True, res["uncertain"][0]["uncertain"])
        self.assertEqual(res["hypotheses"], [])  # 降级路径假设为空（0.22）


if __name__ == "__main__":
    unittest.main(verbosity=2)
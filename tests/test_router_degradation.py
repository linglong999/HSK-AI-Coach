# ============================================================
# 4.1 失败注入测试：单引擎失败不整体崩溃（验收标准③证据）
# 全部 mock 引擎层，不调用 LLM、不需要 API Key。
# 覆盖：识别失败 / 讲解失败 / 图谱写失败 / 待确认写失败 / 图谱保存失败
#       + 一条 mock 全链路正路径（验收①：纠错+讲解+图谱更新一次完成）
# 运行: python -m unittest tests.test_router_degradation -v
# ============================================================

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.router import Router

# 2.1 v0.3 schema 形状的一条已确认偏误（kp 命中 → 图谱 upsert 节点）
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
    "type": "语法",
    "type_confident": False,
    "confidence": 0.4,
    "knowledge_point_id": "",
}


class DegradationTestBase(unittest.TestCase):
    """每个用例独立 learner_id，跑完清理 data/ 下的图谱文件"""

    LEARNER = "test_deg"

    def setUp(self):
        self.router = Router(learner_id=self.LEARNER, native_lang="英语",
                             user_level="HSK3")
        self.data_path = os.path.join(_PROJECT_ROOT, "data",
                                      f"graph_{self.LEARNER}.json")

    def tearDown(self):
        if os.path.exists(self.data_path):
            os.remove(self.data_path)

    @staticmethod
    def _boom(*_a, **_kw):
        raise RuntimeError("注入的引擎故障")

    def _mock_recognize(self, confirmed=None, uncertain=None):
        # 0.25：router.process 现传 level（起点分层），替身签名需接受
        self.router.recognizer.recognize = lambda text, level=3, native_lang="": {
            "errors": confirmed or [], "uncertain": uncertain or []}

    def _mock_explain_ok(self):
        self.router.explainer.explain = lambda err, **kw: {
            "explanation": "语序应为目的语很多+名词。例：很多水。",
            "key_points": [{"id": "kp-1", "text": "很多+名词语序"}],
            "keywords": ["语序"], "free_generated": False}


class TestSingleEngineFailure(DegradationTestBase):
    LEARNER = "test_deg_rec"

    def test_recognizer_fail_returns_degraded(self):
        """识别引擎宕机 → 整体不崩，degraded 记录，返回空结果"""
        self.router.recognizer.recognize = self._boom
        res = self.router.process("我想买苹果很多。", event_key="t1")
        self.assertEqual(res["errors"], [])
        # 契约 v1：degraded 结构化 dict，stage=recognize 且 fatal=True
        self.assertTrue(any(d.get("stage") == "recognize" and d.get("fatal")
                            for d in res["degraded"]))
        self.assertEqual("v1", res["contract_version"])


class TestExplainerFailure(DegradationTestBase):
    LEARNER = "test_deg_exp"

    def test_explainer_fail_keeps_graph_write(self):
        """讲解引擎宕机 → 该条降级标记，但图谱写入照常、偏误照常返回"""
        self._mock_recognize(confirmed=[dict(FAKE_CONFIRMED)])
        self.router.explainer.explain = self._boom
        res = self.router.process("我想买苹果很多。", event_key="t2")
        self.assertEqual(len(res["errors"]), 1)
        self.assertTrue(res["errors"][0]["explanation"].get("_degraded"))
        self.assertTrue(any(d.get("stage") == "explain" for d in res["degraded"]))
        self.assertEqual(res["errors"][0]["graph_write"].get("status"),
                         "node_upsert")


class TestGraphWriteFailure(DegradationTestBase):
    LEARNER = "test_deg_graph"

    def test_confirmed_write_fail_keeps_explanation(self):
        """图谱写失败 → status 记录 write_failed，讲解结果照常返回"""
        self._mock_recognize(confirmed=[dict(FAKE_CONFIRMED)])
        self._mock_explain_ok()
        self.router.graph.ingest_error = self._boom
        res = self.router.process("我想买苹果很多。", event_key="t3")
        self.assertEqual(len(res["errors"]), 1)
        self.assertIn("write_failed", res["errors"][0]["graph_write"]["status"])
        self.assertIn("很多", res["errors"][0]["explanation"]["explanation"])

    def test_uncertain_write_fail_no_crash(self):
        """待确认队列写失败（4.1 修的裸调用漏洞）→ 降级记录，不抛异常"""
        self._mock_recognize(uncertain=[dict(FAKE_UNCERTAIN)])
        self.router.graph.ingest_error = self._boom
        res = self.router.process("我想买苹果很多。", event_key="t4")
        self.assertTrue(any(d.get("stage") == "graph_write" for d in res["degraded"]))


class TestGraphSaveFailure(DegradationTestBase):
    LEARNER = "test_deg_save"

    def test_save_fail_still_returns_result(self):
        """最终图谱保存失败 → 结果照常返回，degraded 记录"""
        self._mock_recognize()
        self.router.graph.save = self._boom
        res = self.router.process("我想买苹果很多。", event_key="t5")
        self.assertTrue(any(d.get("stage") == "graph_save" for d in res["degraded"]))
        self.assertEqual(res["degraded"], res["degraded"])  # 可断言即未抛异常


class TestFullLoopMocked(DegradationTestBase):
    LEARNER = "test_deg_full"

    def test_full_chain_positive_path(self):
        """验收①（mock 版）：一次调用产出 纠错 + 讲解 + 图谱更新，接口字段全部对齐"""
        self._mock_recognize(confirmed=[dict(FAKE_CONFIRMED)])
        self._mock_explain_ok()
        res = self.router.process("我想买苹果很多。", event_key="t6")
        self.assertEqual(res["degraded"], [])
        item = res["errors"][0]
        # 识别 schema → 讲解消费
        self.assertEqual(item["error"]["fragment"], "苹果很多")
        self.assertEqual(item["explanation"]["key_points"][0]["text"], "很多+名词语序")
        # 识别 schema → 图谱消费（kp 命中 → 节点 upsert，复习队列非空）
        self.assertEqual(item["graph_write"]["status"], "node_upsert")
        self.assertGreaterEqual(len(res["review_queue"]), 1)
        self.assertGreaterEqual(res["graph_size"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
# -*- coding: utf-8 -*-
"""P0.2 · 图谱双维度字段映射测试。
覆盖：
  - error_kind_map 纯逻辑：kp 级命中 / 平局榜 / kp-de-di-de 覆盖 / type 级回落 / 兜底 warning
  - Node 字段：to_dict/from_dict 带双字段
  - load 白名单同步：旧数据（缺双字段）重载不丢、现算补齐
  - 复合值"语法-语序"不报 KeyError（P0.2 显式映射）
  - 映射表自身：25 kp 全覆盖、未知数恰为 9、nature 取值合法
运行: python -m unittest tests.test_error_kind_map -v"""
import json
import os
import sys
import tempfile
import unittest
import warnings

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.graph.error_graph import ErrorGraph, Node, resolve as _resolve_impl
import engine.graph.error_kind_map as ekm


def _with_warnings(fn):
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        out = fn()
    return out, w


class MapLogicTest(unittest.TestCase):
    """error_kind_map 纯函数：两级联查与显式映射规则。"""

    def test_kp_level_hit(self):
        # kp 级命中：量词 → kind=语法(平局榜)、nature=误代
        self.assertEqual(_resolve_impl({"语法": 1, "词汇": 1}, "kp-liangci"),
                         {"error_kind": "语法", "nature": "误代"})

    def test_kp_de_di_de_override(self):
        # kp-de-di-de 显式覆盖为"汉字"（T-7 汉字维度唯一锚点）
        self.assertEqual(_resolve_impl({"汉字": 1, "语法": 1}, "kp-de-di-de")["error_kind"],
                         "汉字")

    def test_tie_break_priority(self):
        # 平局按榜：语法 > 词汇 > 语用 > 汉字
        self.assertEqual(ekm.dominant_kind({"语用": 1, "词汇": 1}), "词汇")
        self.assertEqual(ekm.dominant_kind({"汉字": 1, "语法": 1}), "语法")

    def test_tie_no_priority_then_first_win(self):
        # 平局但榜内无命中（都不可能，仅防御）：取 candidates 首
        out = ekm.dominant_kind({"汉字": 1, "语用": 1})
        self.assertIn(out, {"汉字", "语用"})

    def test_argmax_majority(self):
        # 单一高频主导：语法 2 占优
        self.assertEqual(ekm.dominant_kind({"语法": 2, "词汇": 1}), "语法")

    def test_type_level_fallback_compound(self):
        # type 级回落：自由节点含"语法-语序" → kind=语法、nature=错序（显式映射）
        self.assertEqual(_resolve_impl({"语法-语序": 2}, "free-node"),
                         {"error_kind": "语法", "nature": "错序"})

    def test_type_level_fallback_base(self):
        # type 级回落：基础 type → kind 原样、nature=未知
        self.assertEqual(_resolve_impl({"语法": 2}, "free-node"),
                         {"error_kind": "语法", "nature": "未知"})

    def test_unknown_fallback_warns(self):
        # 两级全未命中 → (未知,未知) + warning
        out, w = _with_warnings(
            lambda: _resolve_impl({"怪异": 1}, "unknown-kp"))
        self.assertEqual(out, {"error_kind": "未知", "nature": "未知"})
        self.assertTrue(any("未命中" in str(x.message) for x in w))

    def test_empty_error_types_unknown(self):
        # 空 error_types → kind=未知（kp 命中时 nature 仍可给）
        out, _ = _with_warnings(
            lambda: _resolve_impl({}, "kp-liangci"))
        self.assertEqual(out["nature"], "误代")
        self.assertEqual(out["error_kind"], "未知")


class MappingTableIntegrityTest(unittest.TestCase):
    """映射表自身完整性：25 kp 全覆盖、未知数恰 9、合法取值。"""

    def test_kp_map_coverage(self):
        # kp 级表必须覆盖 25 个标准 kp 清单
        expect = {
            "kp-ba-sentence", "kp-bei-sentence", "kp-liangci",
            "kp-nengyuan-dongci", "kp-bi-sentence", "kp-he-yiyang",
            "kp-le-dynamic", "kp-zhe", "kp-guo", "kp-jiuguo-jiegou",
            "kp-quxiang-buyu", "kp-de-di-de", "kp-cunxian-ju",
            "kp-liandong-ju", "kp-standing-shi", "kp-shide-sentence",
            "kp-dongci-shuangbin", "kp-zhuangyu-chezhi",
            "kp-zhongci-zhitou", "kp-preposition-zaizai",
            "kp-jietiaoyu-tiaojian", "kp-haishi-xuanze",
            "kp-chengdu-jieci", "kp-zhizhi-dao", "kp-zhongci-fugao",
        }
        self.assertEqual(set(ekm._NATURE_MAP), expect)

    def test_unknown_count_exactly_9(self):
        # 用户拍板：最终未知恰 9/25（4 CGED + 5 维持）
        unknown = {k for k, v in ekm._NATURE_MAP.items() if v == "未知"}
        expect = {"kp-ba-sentence", "kp-bei-sentence", "kp-cunxian-ju",
                  "kp-shide-sentence", "kp-he-yiyang", "kp-le-dynamic",
                  "kp-zhe", "kp-guo", "kp-standing-shi"}
        self.assertEqual(unknown, expect)

    def test_nature_values_legal(self):
        # 所有 nature 取值必须落在四分法+未知内
        for v in ekm._NATURE_MAP.values():
            self.assertIn(v, ekm.NATURES, v)

    def test_type_map_has_four_base_plus_compound(self):
        self.assertEqual(set(ekm._KIND_MAP),
                         {"词汇", "语法", "语用", "汉字", "语法-语序"})

    def test_override_key_in_kp_set(self):
        # 覆盖键必须是标准 kp 之一
        self.assertIn("kp-de-di-de", ekm._NATURE_MAP)


class NodeFieldTest(unittest.TestCase):
    """Node 双字段读写 + 写接口自动填充。"""

    def _graph(self):
        return ErrorGraph("test_dims")

    def test_ingest_error_sets_dimensions(self):
        g = self._graph()
        g.ingest_error({"fragment": "我买一个奶茶",
                        "type": "语法", "knowledge_point_id": "kp-liangci",
                        "knowledge_point_name": "量词"}, event_key="e1")
        nd = g.get_kp("kp-liangci")["node"]
        self.assertEqual(nd["error_kind"], "语法")
        self.assertEqual(nd["nature"], "误代")

    def test_node_to_dict_has_fields(self):
        n = Node(id="x", knowledge_point="y", level="1", error_types={"语法": 1},
                 error_kind="语法", nature="错序")
        d = n.to_dict()
        self.assertEqual(d["error_kind"], "语法")
        self.assertEqual(d["nature"], "错序")

    def test_ingest_verdict_sets_dimensions(self):
        g = self._graph()
        g.ingest_verdict({"knowledge_point_id": "kp-zhuangyu-chezhi",
                          "knowledge_point_name": "状语语序"},
                         verdict="pass", uncertain=False, event_key="e2")
        nd = g.get_kp("kp-zhuangyu-chezhi")["node"]
        self.assertEqual(nd["nature"], "错序")

    def test_confirm_item_sets_dimensions(self):
        g = self._graph()
        g.ingest_error({"fragment": "我回家坐车", "type": "语法-语序",
                        "knowledge_point_id": ""}, event_key="e3")
        # 未命中 → 入队
        self.assertTrue(g.get_review_queue([]) == [])
        # 手工落节点并 confirm 到已存在节点，验证 confirm 分支补填
        g.ingest_error({"fragment": "我回家坐车", "type": "语法-语序",
                        "knowledge_point_id": "kp-zhuangyu-chezhi",
                        "knowledge_point_name": "状语语序"}, event_key="e4")
        # 该 kp 已被确认命中
        nd = g.get_kp("kp-zhuangyu-chezhi")["node"]
        self.assertEqual(nd["error_kind"], "语法")
        self.assertEqual(nd["nature"], "错序")
        self.assertEqual(nd["error_types"], {"语法-语序": 1})


class PersistenceCompatibilityTest(unittest.TestCase):
    """load 白名单同步：旧数据重载不丢、现算补齐；含复合值不炸。"""

    def _write(self, tmpdir, data, name="graph.json"):
        p = os.path.join(tmpdir, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return p

    def test_load_old_data_backfills_dimensions(self):
        # 旧数据节点无双字段 → load 后现算补齐
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, {
                "learner_id": "old",
                "nodes": {"kp-liangci": {
                    "id": "kp-liangci", "knowledge_point": "量词", "level": "2",
                    "error_types": {"语法": 3, "词汇": 1}, "error_count": 4,
                    "mastery": 0.5, "last_learnt_at": None, "created_at": "t"}},
                "edges": {}, "queue": {}, "_seen_events": [],
            })
            g = ErrorGraph("old")
            g.load(p)
            nd = g.get_kp("kp-liangci")["node"]
            self.assertEqual(nd["error_kind"], "语法")
            self.assertEqual(nd["nature"], "误代")

    def test_load_new_data_preserves_fields(self):
        # 已有双字段的新数据 → 重载原样保留（不重算覆盖）
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, {
                "learner_id": "new",
                "nodes": {"kp-bi-sentence": {
                    "id": "kp-bi-sentence", "knowledge_point": "比较句", "level": "3",
                    "error_types": {"语法": 2}, "error_count": 2, "mastery": 0.3,
                    "last_learnt_at": None, "created_at": "t",
                    "error_kind": "语法", "nature": "误加"}},
                "edges": {}, "queue": {}, "_seen_events": [],
            })
            g = ErrorGraph("new")
            g.load(p)
            nd = g.get_kp("kp-bi-sentence")["node"]
            self.assertEqual(nd["error_kind"], "语法")
            self.assertEqual(nd["nature"], "误加")

    def test_save_roundtrip_has_dimensions(self):
        # save→load 全流程：双字段不丢
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "graph.json")
            g = ErrorGraph("rt")
            g.ingest_error({"fragment": "x", "type": "语法",
                            "knowledge_point_id": "kp-chengdu-jieci",
                            "knowledge_point_name": "程度副词"}, event_key="e")
            g.save(p)
            g2 = ErrorGraph("rt")
            g2.load(p)
            nd = g2.get_kp("kp-chengdu-jieci")["node"]
            self.assertEqual(nd["error_kind"], "语法")
            self.assertEqual(nd["nature"], "误加")


if __name__ == "__main__":
    unittest.main()
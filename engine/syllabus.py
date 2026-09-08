# -*- coding: utf-8 -*-
# ============================================================
# engine/syllabus.py —— 新 HSK 考纲运行时（P0.15，任务型教学内容主干）
#
# 数据源：krmanik/HSK-3.0 的 New HSK (2025)/HSK Grammar/json/
#   7 个官方制品（HSK 1-6 单级 + HSK 7-9 合编），593 条原始行全量。
#   字段仅 类别 / 类别名称 / 细目 / 语法内容 四列，无级别字段——
#   级别由文件归属决定。7-9 合编文件用 level="7-9" 合编级表示，
#   by_level(7/8/9) 归一命中（三等九级检索下视为同一高级档）。
#
# 入库产物：datasets/syllabus_hsk30_2025.json（两段式 after 人工转正，
#   见 scripts/build_syllabus_candidates.py）。每条：
#     id            hsk30-g{39}-{nn} 或 hsk30-g79-{nn}（7-9 合编前缀）
#     category      类别（语素/词类/短语/句子成分/句子的类型/动作的态/特殊表达法）
#     name          类别名称
#     item          细目（可为空）
#     grammar       "语法内容"
#     level         int(1-6) 或 "7-9"（合编）
#     level_gf      int(1-9)；7-9 合编为 None（不强行标单一 GF）
#     band          "初等"/"中等"/"高等"；7-9 合编恒为 "高等"
#     prereq        list[str] 前置依赖知识 id（首批人工标，可空）
#     desc          str 教学描述（P0.13 造题用）
#     examples      list[str] 例句（本轮预留空数组，P0.13 喂料）
#     scene_tags    list[str] 场景标签（本轮预留）
#     question_tags list[str] 题型标签（本轮预留）
#     status        "candidates"|"approved"（两段式入库标记）
# ============================================================

import json
import os
import re

from .levels import hsk3_to_gf, band_name

# 检索归一化用：去 引号/书名号/括号/空白/破折号，避免 "“把”字句1" 阻断子串
_PUNCT = re.compile(r"[\s“”‘’\"'《》()（）…—]")

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "datasets", "syllabus_hsk30_2025.json")

# 7-9 合编对外做"三等九级"检索时归一带命中的级
G79_LOW, G79_HIGH = 7, 9
G79_BAND = "高等"


class SyllabusError(Exception):
    pass


class SyllabusDatabase:
    """考纲数据只读运行时。入库由 build 脚本负责，此处显式只读。"""

    def __init__(self, points):
        self._points = points          # list[dict] 原样引用
        self._by_id = {p["id"]: p for p in points}
        self._by_level = {}
        for p in points:
            self._by_level.setdefault(p["level"], []).append(p)

    @classmethod
    def load(cls, path=DATA_PATH):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(data.get("points", []))

    def all(self, status=None):
        if status is None:
            return self._points
        return [p for p in self._points if p.get("status") == status]

    def by_id(self, sid):
        """精确 id 命中；未命中抛错（宁错勿静默）。"""
        if sid not in self._by_id:
            raise SyllabusError("unknown syllabus id: %r" % sid)
        return self._by_id[sid]

    def by_level(self, level):
        """按级别检索；7-9 合编按三等九级归一命中(7/8/9 均返回合编档)。"""
        if isinstance(level, int) and G79_LOW <= level <= G79_HIGH:
            return list(self._by_level.get("7-9", []))
        return list(self._by_level.get(level, []))

    def search(self, keyword, field=None):
        """模糊检索。field 限 null(全文)/category/name/item/grammar。
        7-9 无级别字段但其余四列可检索。对引号/书名号归一后比对，
        故 "把字句" 可命中 "“把”字句1"。"""
        kw = _PUNCT.sub("", keyword).lower()
        if not kw:
            return []
        result = []
        for p in self._points:
            if field is not None:
                text = _PUNCT.sub("", str(p.get(field, ""))).lower()
                if kw in text:
                    result.append(p)
            else:
                hay = _PUNCT.sub("", " ".join(
                    str(p.get(k, "")) for k in ("category", "name", "item", "grammar"))).lower()
                if kw in hay:
                    result.append(p)
        return result

    def __len__(self):
        return len(self._points)


def build_point(row, level, index):
    """单条 JSON 原始行 → 入库 dict。level: int(1-6) 或 "7-9"。"""
    if level == "7-9":
        sid = "hsk30-g79-%03d" % index
        gf = None
        band = G79_BAND
    else:
        sid = "hsk30-g%d-%03d" % (level, index)
        gf = hsk3_to_gf(level)
        band = band_name(gf)
    return {
        "id": sid,
        "category": row.get("类别", ""),
        "name": row.get("类别名称", ""),
        "item": row.get("细目", ""),
        "grammar": row.get("语法内容", ""),
        "level": level,
        "level_gf": gf,
        "band": band,
        "prereq": [],
        "desc": "",
        "examples": [],
        "scene_tags": [],
        "question_tags": [],
        "status": "candidates",
    }
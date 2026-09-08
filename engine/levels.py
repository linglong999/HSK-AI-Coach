# -*- coding: utf-8 -*-
# ============================================================
# engine/levels.py —— 等级口径唯一真源（P0.6）
#
# 统一到《国际中文教育中文水平等级标准》（GF 0025-2021）三等九级：
#   初等(1/2/3)、中等(4/5/6)、高等(7/8/9)。
#
# 三张表分工（P0.6 拍板）：
#   GF_BANDS      ：GF 级 → 等名（图谱/画像/前端共用）
#   HSK_TO_GF     ：HSK 3.0 大纲级号(1-9) → GF 级。仅在 P0.15 krmanik
#                   考纲入库时使用（新大纲 HSK 级号=GF 级，一一对应）。
#   LEGACY_TO_GF  ：旧 1-4 粗分级 → GF 级。现有图谱/知识点库迁移走这张
#                   （知识点库 level 是 int 粗分，build_portal 拼成
#                   "HSK{int}级" 注入，故图谱 "HSK3" = 旧粗分 3 = GF5，
#                   严禁误用 HSK_TO_GF 否则会标低 2 级）。
#
# 注意：本模块的"旧粗分"仅指知识点元数据的 1-4 精麻分，与 learners 侧
#       user_level（0.25 起点的 HSK1-6，同是旧粗分语义字符串）语义承接但
#       职责不同，本次不混改。
# ============================================================

import re

# GF 级 → 等名
GF_BANDS = {1: "初等", 2: "初等", 3: "初等",
            4: "中等", 5: "中等", 6: "中等",
            7: "高等", 8: "高等", 9: "高等"}

# HSK 3.0 大纲级号 → GF 级（仅服务 P0.15 krmanik 考纲入库）
HSK_TO_GF = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9}

# 旧 1-4 粗分 → GF 级（现有图谱/知识点库迁移专用；一对多取舍：
#   旧 1→GF1（初等入门）、旧 2→GF3（初等收尾）、旧 3→GF5（中等中段）、
#   旧 4→GF6（中等顶端）——取各粗分段的中位代表性 GF 级，避免过度细分）
LEGACY_TO_GF = {1: 1, 2: 3, 3: 5, 4: 6}


def _normalize(value) -> "int|None":
    """兼容五种输入形态（P0.6 追加）：
      int(3)/"3"(字符串数字)/"HSK3"(大写HSK+数字)/"未知"/None(缺省)
    解析出"旧粗分整数"(1-4)；无法识别返回 None。之外不做 GF 换算。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 4 else None
    if isinstance(value, float) and value.is_integer():
        v = int(value)
        return v if 1 <= v <= 4 else None
    if isinstance(value, str):
        s = value.strip()
        m = re.fullmatch(r"[Hh][Ss][Kk]?(\d+)", s)   # "HSK3"/"HSK3级" 前缀
        if m:
            n = int(m.group(1))
            return n if 1 <= n <= 4 else None
        if s.isdigit():
            n = int(s)
            return n if 1 <= n <= 4 else None
        low = s.lower()
        if low in ("未知", "unknown", "", "none"):
            return None
    return None


def legacy_to_gf(value):
    """旧 1-4 粗分 → GF 级。兼容五态输入；未知/越界返回 None。"""
    n = _normalize(value)
    if n is None:
        return None
    return LEGACY_TO_GF.get(n)


def hsk3_to_gf(value):
    """HSK 3.0 大纲级号 → GF 级（服务 P0.15 krmanik）。收 int 1-9。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 1 <= value <= 9:
        return HSK_TO_GF[value]
    if isinstance(value, str) and value.strip().isdigit():
        n = int(value.strip())
        return HSK_TO_GF.get(n)
    return None


def band_name(gf: "int|None") -> str:
    """GF 级 → 等名（'初等'/'中等'/'高等'）；None/越界 → '未定'。"""
    if gf is None:
        return "未定"
    return GF_BANDS.get(gf, "未定")
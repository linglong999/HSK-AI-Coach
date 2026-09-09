# ============================================================
# error_kind_map.py
# P0.2 · 图谱双维度字段映射表（确定性，非 Agent）
# 给偏误图谱节点加两个正交维度：
#   error_kind —— 语言要素主导维度（词汇/语法/语用/汉字）
#   nature     —— 鲁健骥四分法（遗漏/误加/误代/错序/未知）
# 规则（用户 P0.2 拍板）：
#   - error_kind 单值 = argmax(error_types)（"主导类型"语义）；
#     平局按榜单 KIND_PRIORITY；kp-de-di-de 显式覆盖为"汉字"。
#   - mapping 两级联查：键 = kp_id → 未命中回落 type 级 → 再未命中 → (未知,未知)+warning。
#   - 显式映射；未知复合值一律 (未知,未知)+warning，禁止隐式拆分。
# ============================================================

import warnings
from typing import Dict, Optional, Tuple

# error_kind 主导维度取值（与 recognizer 权威枚举一致）
ERROR_KINDS = ("词汇", "语法", "语用", "汉字")

# 平局仲裁榜：error_types 双标签同频时取榜中最高维（用户拍板点1）。
# 依据：更高维度（语法 > 词汇/语用）能覆盖较低维度的子类，作为主导最稳。
KIND_PRIORITY = ("语法", "词汇", "语用", "汉字")

# error_kind 显式覆盖：kp-de-di-de 是 T-7 汉字维度唯一锚点，
# 混写本质是书写问题，语法优先会让汉字维度空转——所以强制归"汉字"。
KIND_OVERRIDE = {
    "kp-de-di-de": "汉字",
}

# ------------------------------------------------------------
# 第一层：type 级映射表（5 条）
# error_kind：复合可用值在此归一回归大类（见 _TYPE_NORMALIZE）；
# nature：基础维度过宽，无法仅凭 type 判四分法 → 显式"未知"。
# ------------------------------------------------------------
# 取值 = {"nature": ..., "note": 判断依据一句话}
_KIND_MAP = {
    "词汇": {"nature": "未知", "note": "词汇维度，误加/误代取决于具体用词，仅凭 type 不可判"},
    "语法": {"nature": "未知", "note": "语法维度，四分法需细化到语法点，仅凭 type 不可判"},
    "语用": {"nature": "未知", "note": "语用维度，误选或遗漏依语境而定"},
    "汉字": {"nature": "未知", "note": "汉字维度，形误/义误需细看"},
    "语法-语序": {"nature": "错序", "note": "用户拍板：语序错误稳定对应鲁健骥'错序'"},
}

# 复合可用值 → 归一 error_kind 大类（当前仅"语法-语序"一种）
_TYPE_NORMALIZE = {
    "语法-语序": "语法",
}

# ------------------------------------------------------------
# 第二层：kp 级映射表（25 条）
# 键 = kp_id，值 = nature 主倾向。
# 标注：未知 = 该 kp 主倾向不唯一（用户画像：不硬塞，unknown 走 P0.4 默认权重路径）。
# 四句式未知依据：CGED 14,517 句实测（raw_type 对应 R 误加/M 遗漏/S 误代/W 顺序）、
#   错序在把/被/存现/强调四分中稳定垫底（最高 12.1%），各句式呈回避/误加/误代三分或双源冲突。
# ------------------------------------------------------------
_NATURE_MAP = {
    # —— 用户 CGED/文献裁定为未知（4 条）——
    "kp-ba-sentence": "未知",      # CGED：把字句错序占比低，呈回避/误加双源，主倾向不唯一
    "kp-bei-sentence": "未知",     # CGED：被字句同，错序垫底，主倾向不唯一
    "kp-cunxian-ju": "未知",       # CGED：存现句回避为主，错序非首因
    "kp-shide-sentence": "未知",   # CGED：'是…的'误加/遗漏双源冲突，主倾向不唯一

    # —— 原本未知，维持（5 条）——
    "kp-he-yiyang": "未知",        # 常与比较句混淆致误代，但需语境判，主倾向不唯一
    "kp-le-dynamic": "未知",       # 完成态漏用=遗漏，过用=误加，两倾向并存
    "kp-zhe": "未知",              # 持续态漏用=遗漏，与'了'混=误代，不唯一
    "kp-guo": "未知",              # 经历态漏用=遗漏，与'了'混=误代，不唯一
    "kp-standing-shi": "未知",     # 请/让/叫该用未用=遗漏，错类型=误代，不唯一

    # —— 有明确主倾向（16 条）——
    "kp-liangci": "误代",                # 量词混用（个代张/本）→误代
    "kp-nengyuan-dongci": "误代",        # 能/会/可以混用→误代
    "kp-bi-sentence": "误加",            # 比较句程度副词冗余（A比B…很）→误加
    "kp-jiuguo-jiegou": "遗漏",          # 漏补语（"做"代"做完"）→遗漏
    "kp-quxiang-buyu": "遗漏",           # 漏趋向补语（"回"代"回来"）→遗漏
    "kp-de-di-de": "误代",               # 得/地/的混用→误代
    "kp-liandong-ju": "遗漏",            # 漏第二谓词（"去商店买"）→遗漏
    "kp-dongci-shuangbin": "错序",       # V+人+物语序错（"给书我"）→错序
    "kp-zhuangyu-chezhi": "错序",        # 时间/地点/方式状语位置错→错序
    "kp-zhongci-zhitou": "误代",         # 人称/指示代词混用→误代
    "kp-preposition-zaizai": "误代",     # 在/从/给/向介词混用→误代
    "kp-jietiaoyu-tiaojian": "误代",     # 如果/因为等关联词错配→误代
    "kp-haishi-xuanze": "误代",          # 还是/吗 疑问句式误选→误代
    "kp-chengdu-fuci": "误加",          # 很/太程度副词叠加冗余→误加
    "kp-zhizhi-dao": "错序",             # 时量补语位置/时长句序错→错序
    "kp-zhongci-fugao": "误代",          # 都/也/只/还范围副词混用→误代

    # —— 7-9 转正新 kp（P0.15，全部未知：偏误多源不唯一，用户拍板不硬塞）——
    "kp-chengdu-buyu": "未知",          # 程度补语：与程度副词叠用=误加、程度词混用=误代，双源
    "kp-keneng-buyu": "未知",           # 可能补语：en 高频回避致漏用=遗漏、与能/会混=误代，不唯一
    "kp-dongliang-buyu": "未知",        # 动量补语：漏次/回/遍=遗漏、次回遍混用=误代，en 有 times 非纯漏
}
# 断言覆盖：28 条 kp 必须都在映射表内
_KNOWN_KP_IDS = (
    "kp-ba-sentence", "kp-bei-sentence", "kp-liangci", "kp-nengyuan-dongci",
    "kp-bi-sentence", "kp-he-yiyang", "kp-le-dynamic", "kp-zhe", "kp-guo",
    "kp-jiuguo-jiegou", "kp-quxiang-buyu", "kp-de-di-de", "kp-cunxian-ju",
    "kp-liandong-ju", "kp-standing-shi", "kp-shide-sentence",
    "kp-dongci-shuangbin", "kp-zhuangyu-chezhi", "kp-zhongci-zhitou",
    "kp-preposition-zaizai", "kp-jietiaoyu-tiaojian", "kp-haishi-xuanze",
    "kp-chengdu-fuci", "kp-zhizhi-dao", "kp-zhongci-fugao",
    "kp-chengdu-buyu", "kp-keneng-buyu", "kp-dongliang-buyu",
)
assert set(_NATURE_MAP) == set(_KNOWN_KP_IDS), "kp 级映射表与 28 个 KP 清单不一致"
assert set(_KIND_MAP) == {"词汇", "语法", "语用", "汉字", "语法-语序"}, "type 级映射表条目异常"
assert len([v for v in _NATURE_MAP.values() if v == "未知"]) == 12, \
    "最终未知应为 12/28（4 CGED + 5 维持 + 3 P0.15 新 kp），见 P0.2/P0.15 用户裁定"

NATURES = ("遗漏", "误加", "误代", "错序", "未知")


def _normalize_kind(raw: str) -> str:
    """归一 error_kind 到四大维度；复合可用值回归大类，否则未知。"""
    if raw in ERROR_KINDS:
        return raw
    return _TYPE_NORMALIZE.get(raw, "未知")


def _argmax_raw(error_types: Dict[str, int]) -> str:
    """argmax 原始 type（未归一），平局按 KIND_PRIORITY 榜取最高维。"""
    max_n = max(error_types.values())
    candidates = [t for t, n in error_types.items() if n == max_n]
    if len(candidates) == 1:
        return candidates[0]
    return next((t for t in KIND_PRIORITY if t in candidates), candidates[0])


def dominant_kind(error_types: Dict[str, int], kp_id: Optional[str] = None) -> str:
    """error_kind = argmax(error_types)（主导类型）；kp-de-di-de 显式覆盖。"""
    if kp_id and kp_id in KIND_OVERRIDE:
        return KIND_OVERRIDE[kp_id]
    if not error_types:
        return "未知"
    return _normalize_kind(_argmax_raw(error_types))


def resolve(error_types: Dict[str, int],
           kp_id: Optional[str] = None) -> dict:
    """两级联查：kp_id → type 级 → (未知,未知)+warning。
    返回 {"error_kind", "nature"}。error_kind 独立按 argmax 计算。
    回落 type 级用**原始主导 type**查询（如"语法-语序"→错序，而非归一后的"语法"）。"""
    kind = dominant_kind(error_types, kp_id)
    # ① kp 级：kp_id 命中优先
    nature = _NATURE_MAP.get(kp_id)
    # ② type 级：kp 级未命中才回落，用原始主导 type 查
    if nature is None and error_types:
        nature = _KIND_MAP.get(_argmax_raw(error_types), {}).get("nature")
    # ③ 兜底：(未知,未知) + warning
    if nature is None:
        warnings.warn(f"P0.2 映射未命中: kp_id={kp_id!r}, error_types={error_types} -> (未知,未知)",
                      UserWarning, stacklevel=2)
        nature = "未知"
    return {"error_kind": kind, "nature": nature}


# 便捷独立查表（供外部读）：给定 kp_id 直接取 nature（无 error_types 时）
def nature_for_kp(kp_id: Optional[str]) -> str:
    """kp_id 直达 nature；未命中回落 type 级语义未知（此处无 error_types，一律未知+警告）。"""
    if kp_id in _NATURE_MAP:
        return _NATURE_MAP[kp_id]
    warnings.warn(f"P0.2 kp 级映射未命中 kp_id={kp_id!r} -> 未知", UserWarning, stacklevel=2)
    return "未知"
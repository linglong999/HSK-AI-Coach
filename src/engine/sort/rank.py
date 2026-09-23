# -*- coding: utf-8 -*-
# engine/sort/rank.py —— 排序层（B5 I5）：课程静态序骨架（§3 公式直译）
#
# 形态=运行时函数库（拍板点 3）：协意词量级现算毫秒级；消费方（B7 场景包/
# 复习页/B5 排序注入）import 调用；不落盘（教材线回填后如需缓存可一行追加）。
#
# 排序语义：
#   - 主序 = level 升序（**硬主序，禁越级抬/压级** —— 断言锁死）
#   - 同级破平 = comm_score 降序；scene_weights 未回填 → comm_score ≡ 0.5
#     → 同级退化为 lexicon 原始顺序（sorted 稳定排序，不随机不跳动）
#   - 排序单元跟随 §4a：白名单词（B4 sense_whitelist_v1）→ 义项粒度
#     item="{词}-{sense_id}"；其余词 → 词形 item=word（与调度主键一致）
#   - 义项粒度为软依赖：白名单文件缺席 → 自动退化全词形（σ稳，不阻塞本批）
#   - comm_score 打分管线（方案 A 相关性+对数衰减+封顶）实值待教材线，
#     本批只留接口位：comm_score() 签名 + ≡0.5 兜底
#
# 两层分离（模块 docstring 显式标注）：**语法/构式不进静态序**（§3 L226，
# 靠 §4 螺旋）；**第二弹性序（复现/掌握度）不进静态序**——归 §4 动态调度。

import json
import os
from typing import Dict, List, Optional

from config.paths import PROJECT_ROOT

DEFAULT_WHITELIST_PATH = PROJECT_ROOT / "datasets" / "sense_whitelist_v1.json"
DEFAULT_COMM_SCORE = 0.5   # 未回填：同级各词并列，退化为 lexicon 原序


def comm_score(word: str, scene_weights: Optional[dict] = None) -> float:
    """打分管线接口位（方案 A：相关性 + 对数衰减 + 封顶）。实值待教材线恢复后
    才回填（不卡本批）；本批恒 ≡DEFAULT_COMM_SCORE（0.5），于是同级退化为
    lexicon 原始顺序——稳定、不随机、不跳动。"""
    _ = word, scene_weights   # 签名占位；教材线回填时在此实现实际打分
    return DEFAULT_COMM_SCORE


def load_sense_whitelist(path=None) -> Optional[dict]:
    """加载 B4 产物 sense_whitelist_v1.json。
    返回 {词: {"level", "sense_count", "sense_ids"}}；文件缺失/解析失败 → None
    （调用方 key=白名单缺席 → 退化全词形，**非硬依赖**）。"""
    p = path if path is not None else DEFAULT_WHITELIST_PATH
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("words") or {}
    except (OSError, ValueError):
        return None


def _expand_items(lexicon: dict, whitelist: Optional[dict]) -> List[dict]:
    """把 lexicon 词表展开为排序单元列表（词形 + 白名单义项粒度）。
    返回 [{item, level, word, sense_id}]：
      - 白名单词（word 在 whitelist）→ 每义项一条，item="{word}-{sense_id}"
      - 其余词 → 词形一条，item=word, sense_id=None
    whitelist=None（缺席）→ 全部词形粒度。"""
    if whitelist is None:
        whitelist = {}
    word_level = lexicon.get("word_level", {})
    items: List[dict] = []
    for word, lvl in word_level.items():
        meta = whitelist.get(word)
        if meta and meta.get("sense_ids"):
            for sid in meta["sense_ids"]:
                # sense_id 已含词前缀（如"一-01"），item 直接用它（与调度主键一致）
                items.append({"item": sid, "level": lvl,
                              "word": word, "sense_id": sid})
        else:
            items.append({"item": word, "level": lvl, "word": word, "sense_id": None})
    return items


def rank_syllabus(lexicon: dict, scene_weights: Optional[dict] = None,
                  whitelist_path=None) -> List[dict]:
    """产出 ranked_syllabus: [{item, level, comm_score, rank}]。
    - 主序 = level 升序（硬主序，禁越级抬/压级）
    - 同级破平 = comm_score 降序；comm≡0.5 时退化为 lexicon 原序（稳定排序）
    - 白名单词 → 义项粒度（软依赖，缺席退化全词形）
    rank 从 1 起每项递增（同级并列固定排序下 rank 唯一）。"""
    whitelist = load_sense_whitelist(whitelist_path)
    items = _expand_items(lexicon, whitelist)
    # 主序 level 升序；同级用 comm_score 降序破平（同为 0.5 时保持原序稳定）
    items.sort(key=lambda it: (it["level"], -comm_score(it["word"], scene_weights)))
    result = []
    for i, it in enumerate(items, start=1):
        result.append({
            "item": it["item"],
            "level": it["level"],
            "comm_score": comm_score(it["word"], scene_weights),
            "rank": i,
        })
    return result


def levels_of(ranked: List[dict]) -> List[int]:
    """提取主序等级序列（供禁越级断言：必须非递减）。"""
    return [r["level"] for r in ranked]
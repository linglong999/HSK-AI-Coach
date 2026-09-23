# -*- coding: utf-8 -*-
"""B4 H3 · 多义词白名单生成（D-5 §4a，结构先行，gloss 留空）

从义项分拣扫描结果筛"义项数≥5 的 HSK1-2 高频词"→ 产出 `sense_whitelist_v1.json`。
- 数据源    : datasets/docs/sense_split_scan.json 的 high_sense（{词: 义项数}）
- 等级源    : datasets/lexicon_hsk1_4.json 的 word_level（HSK1-2 保留，3-4/缺省剔除）
- 义项序    : 按 scan 义项主次序，sense_id = "{词}-{序号 2 位}"（中心→扩展）
- 结构先行  : gloss/example 留空串，义项内容后续教材线/RAG 回填（CC-CEDICT 原文件
             已不在工作区，超纲重切非本批依赖）
- 可复现    : 固定 seed 落产物 metadata；量级 ≤ 数百

产出: datasets/sense_whitelist_v1.json
    {"generated_at": ..., "seed": 0, "level_filter": [1,2], "min_senses": 5,
     "words": {"打": {"level": 1, "sense_ids": ["打-01", ...], "sense_count": n}, ...}}
运行: python tools/gen_sense_whitelist.py
"""
import json
import os
import random
from datetime import datetime, timezone
from typing import Dict

SEED = 20260923
MIN_SENSES = 5
LEVEL_FILTER = ("1", "2")          # HSK1-2（词频近似=等级，低级=高频）

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN = os.path.join(ROOT, "datasets", "docs", "sense_split_scan.json")
LEXICON = os.path.join(ROOT, "datasets", "lexicon_hsk1_4.json")
OUT = os.path.join(ROOT, "datasets", "sense_whitelist_v1.json")


def _load_lexicon_path(path: str) -> Dict[str, str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("word_level", {})


def _main_word(word: str) -> str:
    # lexicon 词条可能含异体标记（如 "爸爸∣爸"）→ 取主形
    return word.split("∣")[0]


def main() -> None:
    random.seed(SEED)
    with open(SCAN, encoding="utf-8") as f:
        scan = json.load(f)
    high_sense = scan.get("high_sense", {})
    wl = _load_lexicon_path(LEXICON)

    words: Dict[str, dict] = {}
    for word, count in high_sense.items():
        main = _main_word(word)
        level = str(wl.get(main, wl.get(word, "")))
        if level not in LEVEL_FILTER:
            continue
        if int(count) < MIN_SENSES:
            continue
        sense_ids = [f"{main}-{i:02d}" for i in range(1, int(count) + 1)]
        words[main] = {"level": int(level), "sense_count": int(count),
                       "sense_ids": sense_ids}

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": SEED,
        "level_filter": sorted(int(x) for x in LEVEL_FILTER),
        "min_senses": MIN_SENSES,
        "words": dict(sorted(words.items())),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"sense_whitelist_v1: {len(words)} words → {OUT}")


if __name__ == "__main__":
    main()
# ============================================================
# 转化：HSK1-4 权威字表/词表 Excel → 权威 Lexicon JSON
# 来源：教育部《国际中文教育中文水平等级标准》GF 0025-2021（HSK-official版）
# 产出：datasets/lexicon_hsk1_4.json
#   词汇表(3245) + 字表(1261)，带权威分级，供 beyond_level 超纲检测真源
# ============================================================

import json
import os
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "HSK1-4_字表词表_GF0025-2021.xlsx")
OUT = os.path.join(BASE, "lexicon_hsk1_4.json")

import openpyxl
wb = openpyxl.load_workbook(SRC, read_only=True)

LEVEL_NUM = {"一级": 1, "二级": 2, "三级": 3, "四级": 4}


def extract(sheet, cols):
    rows = []
    ws = wb[sheet]
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True)):
        r = list(row)
        if not any(r):
            continue
        rows.append({cols[j]: r[j] for j in range(len(cols)) if j < len(r)})
    return rows


words = extract("词汇表_HSK1-4", ["no", "level", "word", "pinyin", "pos"])
chars = extract("汉字表_HSK1-4", ["no", "level", "char", "pinyin"])

# 构造成 {"词/字": 等级数字}，重复词/字取「最低等级」（一个词跨多级，取最早最低级，判断超纲更严格）
word_level = {}
level_counts = Counter()
for w in words:
    lvl = LEVEL_NUM.get(w.get("level"), 0)
    if w.get("word"):
        word = w["word"].strip()
        prev = word_level.get(word)
        # 只更新：若不存在，或新等级更低（更早出现即更低级原则）
        if prev is None or (prev and lvl < prev):
            word_level[word] = lvl
        level_counts[lvl] += 1

char_level = {}
char_counts = Counter()
for c in chars:
    lvl = LEVEL_NUM.get(c.get("level"), 0)
    if c.get("char"):
        char = c["char"].strip()
        prev = char_level.get(char)
        if prev is None or (prev and lvl < prev):
            char_level[char] = lvl
        char_counts[lvl] += 1

lexicon = {
    "name": "lexicon_hsk1_4",
    "source": "教育部《国际中文教育中文水平等级标准》GF 0025-2021（HSK1-4级）",
    "version": "1.0",
    "note": "权威超纲检测真源。beyond_level=学习者等级 < 内容在词表中的最低等级。词汇3245条/字1261条。与 knowledge_points_v1_4.json（25语法点子集）互补，不替换。",
    "stats": {"word_count": len(word_level), "char_count": len(char_level),
              "word_by_level": dict(level_counts), "char_by_level": dict(char_counts)},
    "word_level": word_level,
    "char_level": char_level,
}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(lexicon, f, ensure_ascii=False, indent=1)

print("词汇表条数:", len(word_level), "等级分布:", dict(level_counts))
print("字表条数:", len(char_level), "等级分布:", dict(char_counts))
print("已写出:", OUT)
print("示例词['苹果']=", word_level.get("苹果"), "示例字['爱']=", char_level.get("爱"))
print("超纲判定示例: HSK1级 出现'感觉'(三级) ->", "感觉" in word_level, word_level.get("感觉") > 1)
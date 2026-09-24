# -*- coding: utf-8 -*-
"""义项分拣·小样本人工判定抽样器（§4a/§4b 切法钉死用）

做法：从 '义项数>=2' 的候选多义词里，按义项数分层随机抽样固定个词，
输出每个词的 CC-CEDICT 全部义项（英文）到 markdown 判定表。
人工/判定方逐词标 Y(需独立调度)/N(不需要)，回填后算教学校准系数，
把词典口径的 2236 折算成教学口径的"真需切义项"数量。

判定口径（写在文档头，供判定方统一）：
- Y: 该词 HSK1-4 教学语境下有 >=2 个义项差异足够大，需独立记忆/调度
- N: 词典义项虽多，但教学语境下实际只教 1 个核心义项，其余是延伸/罕见/异读
"""
import gzip
import json
import os
import random
from collections import Counter

random.seed(20260921)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# B8 换源自 lexicon_hsk1_4.json（2021）→ 3.0（2025）；word_level 键结构同构仅换源。
LEXICON = os.path.join(ROOT, "datasets", "lexicon_hsk3_2025.json")
CEDICT_GZ = os.path.join(os.path.dirname(ROOT), "_tmp_cedict.txt.gz")
OUT = os.path.join(ROOT, "datasets", "docs", "sense_split_sample.md")

def load_cedict(gz_path):
    senses = {}
    with gzip.open(gz_path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "[" not in line or "]" not in line:
                continue
            trad, rest = line.split(" ", 1)
            if " " not in rest:
                continue
            simp = rest.split(" ", 1)[0]
            body = rest.split("]", 1)[1].strip()
            parts = [p.strip() for p in body.split("/") if p.strip()]
            if not parts:
                continue
            senses[simp] = parts  # 保留原始义项串（含 ; 子释义）
    return senses

def load_lexicon(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["word_level"]

def main():
    wl = load_lexicon(LEXICON)
    main_words = {w.split("∣")[0]: lv for w, lv in wl.items()}
    cedict = load_cedict(CEDICT_GZ)

    poly = {w: len(cedict[w]) for w in cedict if w in main_words and len(cedict[w]) >= 2}

    # 分层：义项数 2 / 3 / 4 / 5-8 / 9+，每层抽固定配额
    strata = {"2": 8, "3": 7, "4": 6, "5-8": 6, "9+": 5}
    picked = {}
    for key, quota in strata.items():
        if key == "2":
            pool = [w for w, n in poly.items() if n == 2]
        elif key == "3":
            pool = [w for w, n in poly.items() if n == 3]
        elif key == "4":
            pool = [w for w, n in poly.items() if n == 4]
        elif key == "5-8":
            pool = [w for w, n in poly.items() if 5 <= n <= 8]
        else:
            pool = [w for w, n in poly.items() if n >= 9]
        sample = random.sample(pool, min(quota, len(pool)))
        for w in sample:
            picked[w] = {"senses": len(cedict[w]), "level": main_words[w], "defs": cedict[w]}

    # 写判定表
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    segs = []
    segs.append("# 义项分拣 · 小样本人工判定表（抽样 seed=20260921）\n")
    segs.append("**判定规则**：\n- **Y** = 该词 HSK1-4 教学语境下有 ≥2 个义项，语义/用法差异足够大，需**独立记忆/独立调度**。\n- **N** = 词典义项虽多，但教学语境下实际只教 1 个核心义项，其余为延伸/罕见/异读用法，**不需要独立调度**。\n- 判定依据 = 下方列出的该词在 CC-CEDICT 中的每个义项（英文）。\n")
    # 汇总表
    segs.append("| 词 | 级 | CC义项数 | 判定(Y/N) | 判定理由(≤15字) |\n|---|---|---|---|---|\n")
    for w in sorted(picked, key=lambda x: (picked[x]["level"], x)):
        segs.append(f"| {w} | {picked[w]['level']} | {picked[w]['senses']} |  |  |\n")
    # 义项明细
    segs.append("\n---\n## 义项明细（供逐词判定）\n")
    for w in sorted(picked, key=lambda x: (picked[x]["level"], x)):
        segs.append(f"\n### {w}（HSK{picked[w]['level']}级 · {picked[w]['senses']}义项）\n")
        for i, d in enumerate(picked[w]["defs"], 1):
            segs.append(f"{i}. {d}\n")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(segs))

    from collections import Counter as C2
    lv = C2(picked[w]["level"] for w in picked)
    sn = C2(picked[w]["senses"] for w in picked)
    print(f"抽样总数: {len(picked)}")
    print(f"按级分布: {dict(sorted(lv.items()))}")
    print(f"按义项数分布: {dict(sorted(sn.items()))}")
    print(f"判定表已写: {OUT}")

if __name__ == "__main__":
    main()
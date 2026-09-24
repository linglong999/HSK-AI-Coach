# -*- coding: utf-8 -*-
"""义项分拣成本评估（§4a/§4b 切法前置）

数据源：
- 词表       : datasets/lexicon_hsk3_2025.json (HSK 3.0/2025 1-4 级)
               注：B8 换源自 lexicon_hsk1_4.json（2021，3208 词）→ 3.0（2025）；word_level 键结构同构仅换源。
- 义项标注   : CC-CEDICT（义项以 / 分隔，同义项内释义以 ; 分列）
对照锚点     : CCL-2016 论文 HSK 1-4 重点多义词 434 个

判定口径（§4 立场：先估手工定义多义词义项边界的规模/工作量区间）：
- 候选多义词 = CC-CEDICT 中义项数 >= 2 的词
- 粗略义项数 = 词条的 / 分块数（含 ; 拆分的同义项归并）
- "需切义项的多义词"沿用 CC-CEDICT 义项 >= 2；教学口径由论文 434 校准
"""
import gzip
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# B8 换源自 lexicon_hsk1_4.json（2021）→ 3.0（2025）；word_level 键结构同构仅换源。
LEXICON = os.path.join(ROOT, "datasets", "lexicon_hsk3_2025.json")
CEDICT_GZ = os.path.join(os.path.dirname(ROOT), "_tmp_cedict.txt.gz")

def load_cedict(gz_path):
    """CC-CEDICT: 每行 '传统 简体 [拼音] /义项1/义项2/'，简体为第2字段。"""
    senses = {}
    with gzip.open(gz_path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # 传统 简体 [...
            if "[" not in line:
                continue
            trad, rest = line.split(" ", 1)
            simp = rest.split(" ", 1)[0]
            # 义项：'/'之间
            if "]" not in rest:
                continue
            body = rest.split("]", 1)[1].strip()
            parts = [p.strip() for p in body.split("/") if p.strip()]
            if not parts:
                continue
            # 同义项内以 ';' 分列的子释义，归并算一个义项块
            clusters = []
            for p in parts:
                subs = [s.strip() for s in p.split(";") if s.strip()]
                clusters.append(len(subs))
            senses[simp] = {
                "blocks": len(parts),       # / 分块数（≈义项候选数）
                "subs_total": sum(clusters), # 含同义项拆分的原子释义总数
            }
    return senses

def load_lexicon(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    wl = data["word_level"]
    # 词形可能含 '爸爸∣爸' 之类别名，取主形（'∣'前）
    words = {}
    for form, lv in wl.items():
        main = form.split("∣")[0]
        words[main] = lv
    return words, data.get("stats", {})

def main():
    words, stats = load_lexicon(LEXICON)
    cedict = load_cedict(CEDICT_GZ)
    print(f"词表词数(主形)      : {len(words)}  (lexicon stats word_count={stats.get('word_count')})")
    print(f"CC-CEDICT 可命中     : {sum(1 for w in words if w in cedict)} / {len(words)}")
    miss = [w for w in words if w not in cedict]
    print(f"未命中词形数         : {len(miss)}")

    n_senses = {}  # word -> blocks
    for w in words:
        c = cedict.get(w)
        if c:
            n_senses[w] = c["blocks"]
        else:
            n_senses[w] = 0

    poly = {w: n for w, n in n_senses.items() if n >= 2}
    mono = {w: n for w, n in n_senses.items() if n == 1}
    zero = {w: n for w, n in n_senses.items() if n == 0}

    print("\n===== 分级多义词规模（CC-CEDICT 义项>=2）=====")
    by_lv = Counter()
    for w, n in poly.items():
        by_lv[str(words[w])] += 1
    for lv in ["1", "2", "3", "4"]:
        total = sum(1 for _, l in words.items() if str(l) == lv)
        print(f"  HSK{lv}级: {by_lv[lv]} 多义 / {total} 词  ({by_lv[lv]/total*100:.0f}%)")
    print(f"  HSK1-4 合计: {len(poly)} 多义词")
    print(f"    对比 CCL-2016 论文重点多义词 434")

    print("\n===== 义项数分布（被命中的多义词）=====")
    dist = Counter(n_senses[w] for w in poly)
    for k in sorted(dist):
        if k <= 20 or dist[k] > 3:
            print(f"  {k} 义项: {dist[k]} 词")
    print(f"  >20 义项: 共 {sum(v for k,v in dist.items() if k>20)} 词")

    poly_by_lv = {lv: [w for w,l in words.items() if l==lv and n_senses[w]>=2] for lv in ["1","2","3","4"]}

    print("\n===== 工作量映射（回填 §4 预判线）=====")
    total_blocks = sum(n_senses[w] for w in poly)
    print(f"  需切义项的多义词总数     : {len(poly)}")
    print(f"  多义词平均义项数(blocks) : {total_blocks/len(poly):.1f}")
    print(f"  多义词义项累计(blocks)   : {total_blocks}")
    print(f"  §4b预判线(≤400词?)      : {'✅ 超线/接近 → 需评估属性方案' if len(poly)<=400 else '⚠️ 超400 → §4a属性方案优先'}")
    high = [w for w in poly if n_senses[w] >= 5]
    print(f"  高义项(≥5)词数  : {len(high)}  (人工定义边界工作量主体)")
    print(f"    例: {sorted(high, key=lambda x:-n_senses[x])[:12]}")

    # 输出明细便于复查
    out = os.path.join(ROOT, "datasets", "docs", "sense_split_scan.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "lexicon_stats": stats,
            "hits": sum(1 for w in words if w in cedict),
            "miss_count": len(miss),
            "polymerpuning": {lv: len(poly_by_lv[lv]) for lv in ["1","2","3","4"]},
            "poly_total": len(poly),
            "sense_distribution": {str(k): v for k, v in dist.items()},
            "high_sense": {w: n_senses[w] for w in high},
        }, f, ensure_ascii=False, indent=2)
    print(f"\n明细已存: {out}")

if __name__ == "__main__":
    main()
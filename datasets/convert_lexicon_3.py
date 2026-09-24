# ============================================================
# 转化：HSK 3.0（2025）字词表 txt → 权威 3.0 Lexicon JSON（B8 C1）
# 真源 : krmanik/HSK-3.0（CC BY-SA 4.0，2025 版）：
#      - 词表 HSK Words      /HSK_Level_{1..4}_words.txt    （逐词一行）
#      - 认读 HSK Hanzi      /HSK_Level_{1..4}_hanzi.txt    （逐字一行）
#      - 书写 HSK Handwritten/HSK_Level_{1-2|3|4}_handwritten.txt（逐字一行，1-2 合并标级2）
# 交叉核对 : elkmovie/hsk30（MIT）wordlist.txt / charlist.txt → set 命中率报告
# 产出：datasets/lexicon_hsk3_2025.json（新文件，与 2021 版 lexicon_hsk1_4.json 并存不覆盖）
#   词表纯 3.0、不分轨（{词:级}，键结构与 2021 word_level 完全同构）；
#   字表认读/书写分轨（{char:{recognize:级, write:级|None}}）。
# 拍板锚定：认读 1096 / 书写 400（官方 1-4 累计，社区实测吻合，硬等于断言）；
#           词量约 2000（官方"约"，社区~2030，用范围锚并如实标注差异）。
# 许可披露：产物保留 CC BY-SA 4.0 署名（真源）+ MIT（交叉核对源）。
# 运行：python datasets/convert_lexicon_3.py；消费方换源后本脚本才落地新词表。
# ============================================================

import json
import os
import re
import sys

_RO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RO not in sys.path:
    sys.path.insert(0, _RO)

_RAW = os.path.join(_RO, "datasets", "hsk30_raw")
_KM = os.path.join(_RAW, "krmanik_lexicon")
_EL = os.path.join(_RAW, "elkmovie")
_OUT = os.path.join(_RO, "datasets", "lexicon_hsk3_2025.json")

# ---- 词表 / 认读字 / 书写字源文件 → 等级（1-4 累计同步取）----
_PREFIX_W = "L{}_HSK_Level_{}_words.txt"
_PREFIX_H = "L{}_HSK_Level_{}_hanzi.txt"
_PREFIX_WF = "L{}_HSK_Level_{}_handwritten.txt"   # 1-2 合并：文件前缀 L2_，等级标 2
_HAND = {2: "L2_HSK_Level_1-2_handwritten.txt",
         3: "L3_HSK_Level_3_handwritten.txt",
         4: "L4_HSK_Level_4_handwritten.txt"}

# 官方 1-4 累计锚定（认读/书写硬等于；词量范围）
ANCHOR_RECOGNIZE = 1096
ANCHOR_WRITE = 400
ANCHOR_WORD_LO, ANCHOR_WORD_HI = 1900, 2100  # 官方"约2000"

_WORD_TRAILING_DIGIT = re.compile(r"[0-9]+$")
# 全角括号展开规则：把（...）并入（去括号保留内文）——没（有）→没有、有（一）点儿→有一点儿
_FULL_BRACKET = re.compile(r"（([^（）]*)）")


def _clean_word(raw: str) -> str:
    """krmanik 词条清洗：去尾部数字序号（本1→本）＋展开全角括号（没（有）→没有）。"""
    w = _WORD_TRAILING_DIGIT.sub("", raw)
    w = _FULL_BRACKET.sub(r"\1", w)
    return w.strip()


def _read_lines(path: str):
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


def _load_word_level():
    """{词: 级}，级别取文件级；重复词取最低级（与 2021 更低级原则一致）。"""
    out = {}
    for lv in (1, 2, 3, 4):
        path = os.path.join(_KM, _PREFIX_W.format(lv, lv))
        for raw in _read_lines(path):
            w = _clean_word(raw)
            if not w:
                continue
            prev = out.get(w)
            if prev is None or lv < prev:
                out[w] = lv
    return out


def _load_char_tracks():
    """认读/书写分轨。
    recognize: 认读字表各等级最低级；write: 书写字表各等级最低级。
    认读与书写取并集 → {char:{recognize, write}}。"""
    recognize = {}
    for lv in (1, 2, 3, 4):
        path = os.path.join(_KM, _PREFIX_H.format(lv, lv))
        for c in _read_lines(path):
            prev = recognize.get(c)
            if prev is None or lv < prev:
                recognize[c] = lv
    write = {}
    for lv, fn in _HAND.items():
        path = os.path.join(_KM, fn)
        for c in _read_lines(path):
            prev = write.get(c)
            if prev is None or lv < prev:
                write[c] = lv

    chars = set(recognize) | set(write)
    return {c: {"recognize": recognize.get(c), "write": write.get(c)} for c in chars}


# ---- elkmovie 交叉核对：解析词/字集，算命中率（不与转换产物阻断）----
def _el_parse(path: str, mode: str):
    """elkmovie wordlist="序号 词"；charlist="序号\t字"。剔除页头/空/注释。"""
    out = set()
    with open(path, encoding="utf-8") as f:
        for ln in f:
            s = ln.strip()
            if not s or s.startswith("#") or ("词汇表" in s) or ("汉字表" in s):
                continue
            if mode == "word":
                parts = s.split(None, 1)
                if len(parts) < 2:
                    continue
                raw = parts[1]
                # 拆｜变体 + 去词性括号（白（形）→白）
                for sub in raw.split("｜"):
                    sub = re.sub(r"（[^（）]*）", "", sub).strip()
                    if sub:
                        out.add(sub)
            else:
                parts = s.split()
                if len(parts) >= 2 and parts[1]:
                    out.add(parts[1])
    return out


def _crosscheck(words: set, chars: set):
    """词/字 set 命中率报告。crosscheck_mismatch = 本表有而交叉源无的词/字（仅报告）。"""
    el_words = _el_parse(os.path.join(_EL, "wordlist.txt"), "word") if os.path.isfile(os.path.join(_EL, "wordlist.txt")) else set()
    el_chars = _el_parse(os.path.join(_EL, "charlist.txt"), "char") if os.path.isfile(os.path.join(_EL, "charlist.txt")) else set()

    def hit_rate(ours, theirs):
        if not ours:
            return {"total": 0, "hit": 0, "rate": 0.0}
        hit = sum(1 for w in ours if w in theirs)
        return {"total": len(ours), "hit": hit, "rate": round(hit / len(ours), 4)}

    return {
        "word_hit": hit_rate(words, el_words),
        "char_hit": hit_rate(chars, el_chars),
        "word_mismatch": sorted(words - el_words)[:200],
        "char_mismatch": sorted(chars - el_chars)[:200],
    }


def main():
    word_level = _load_word_level()
    char_level = _load_char_tracks()
    write_chars = {c for c, v in char_level.items() if v["write"] is not None}

    # 分轨统计：认读=recognize 非空数，书写=write 非空数（官方 1096/400 硬等于）
    n_rec = sum(1 for v in char_level.values() if v["recognize"] is not None)
    n_wri = len(write_chars)
    n_word = len(word_level)

    stats = {
        "word_count": n_word,
        "recognize_char_count": n_rec,
        "write_char_count": n_wri,
        "word_by_level": {str(lv): sum(1 for v in word_level.values() if v == lv) for lv in (1, 2, 3, 4)},
        "recognize_by_level": {str(lv): sum(1 for v in char_level.values() if v["recognize"] == lv) for lv in (1, 2, 3, 4)},
        "write_by_level": {str(lv): sum(1 for v in char_level.values() if v["write"] == lv) for lv in (2, 3, 4)},
    }

    # ---- 锚定断言（硬）
    if n_rec != ANCHOR_RECOGNIZE:
        raise SystemExit(f"认读字 {n_rec} != 官方 {ANCHOR_RECOGNIZE}（锚定失败，暂停产出）")
    if n_wri != ANCHOR_WRITE:
        raise SystemExit(f"书写字 {n_wri} != 官方 {ANCHOR_WRITE}（锚定失败，暂停产出）")
    if not (ANCHOR_WORD_LO <= n_word <= ANCHOR_WORD_HI):
        raise SystemExit(f"词量 {n_word} 不在范围 [{ANCHOR_WORD_LO},{ANCHOR_WORD_HI}]（官方约2000，暂停产出）")

    cross = _crosscheck(set(word_level), set(char_level))

    lexicon = {
        "name": "lexicon_hsk3_2025",
        "source": "krmanik/HSK-3.0（New HSK 2025 版）HSK Words/Hanzi/Handwritten 1-4 级 txt，"
                  "https://github.com/krmanik/HSK-3.0（CC BY-SA 4.0）",
        "crosscheck_source": "elkmovie/hsk30 wordlist/charlist（MIT，https://github.com/elkmovie/hsk30）",
        "version": "1.0",
        "note": "HSK 3.0（2025）1-4 级累计词/字表：词不分轨、字认读/书写分轨。与 2021 版 lexicon_hsk1_4.json 并存（可滚回）。",
        "license": "CC BY-SA 4.0（真源 krmanik/HSK-3.0；产物保留署名/相同许可）。交叉核对源 MIT。",
        "stats": stats,
        "crosscheck": {"word_hit": cross["word_hit"], "char_hit": cross["char_hit"]},
        "crosscheck_mismatch": {
            "word": cross["word_mismatch"], "char": cross["char_mismatch"]},
        "word_level": word_level,
        "char_level": char_level,
    }

    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(lexicon, f, ensure_ascii=False, indent=1)

    print("词汇条数:", n_word, "（官方 1-4 约2000），等级分布:", stats["word_by_level"])
    print("认读字:", n_rec, "（锚定 1096），分布:", stats["recognize_by_level"])
    print("书写字:", n_wri, "（锚定 400），分布:", stats["write_by_level"])
    print("交叉核对 word 命中率:", cross["word_hit"], " char 命中率:", cross["char_hit"])
    print("已写出:", _OUT)


if __name__ == "__main__":
    main()
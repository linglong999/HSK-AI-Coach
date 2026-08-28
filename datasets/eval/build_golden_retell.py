# -*- coding: utf-8 -*-
"""
复述黄金集 / 识别扩展 双工具链生成器（v0.1）
- MuCGEC → 复述黄金集（单句 + 多参考修正），天然契合"一个要点一条"复述验证
- CGED   → 识别引擎扩展源（段落切句 + span 按字符重算 + 类型映射），作为识别评测集
- 合规口径：原始语料本地评测/署名引用，不内置 raw 数据分发；本文件为自建标注结构
用法: python datasets/eval/build_golden_retell.py
"""
import json
import os

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_raw_corpus")
RET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "retell_golden.json")
CGD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cged_recognition.json")

# CGED 4 类 → 我们识别引擎的偏误类型映射
CGED_TYPE_MAP = {
    "S": "词汇",  # Selection 误用/选词 → 词汇
    "M": "语法",  # Missing 遗漏 → 语法
    "R": "语法",  # Redundant 冗余 → 语法
    "W": "语法",  # Word order 错序 → 语法
}


def parse_mucgec(path):
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            items.append({
                "source": "mucgec",
                "source_id": parts[0].strip(),
                "raw_sentence": parts[1].strip(),
                "references": [p.strip() for p in parts[2:] if p.strip()],
                "golden_status": "draft",
            })
    return items


def split_sentences(text):
    """简单句切分：按 。！？分出句子，保留标点。返回 [(start_char_idx_in_orig, sentence)]"""
    sents = []
    buf = ""
    start = 0
    i = 0
    for ch in text:
        buf += ch
        if ch in "。！？":
            sents.append((start, buf))
            start = i + 1
            buf = ""
        i += 1
    if buf.strip():
        sents.append((start, buf))
    return sents


def byte_to_char_positions(text):
    """返回累计字符起始的 byte 位置表，用于把 byte 偏移转成 char 索引"""
    acc = 0
    positions = []
    for ch in text:
        positions.append(acc)
        acc += len(ch.encode("utf-8"))
    positions.append(acc)  # 末尾
    return positions


def byte_to_char_idx(text, byte_off):
    """给定 UTF-8 byte 偏移，转成最近的字符串字符索引（落点在该字符内）"""
    positions = byte_to_char_positions(text)
    # 找最大 i 使 positions[i] <= byte_off
    idx = 0
    for i, p in enumerate(positions):
        if p <= byte_off:
            idx = i
    # 若落在两字符间(即前一字符结束点)，归到下一字符更稳妥；但 CGED 表示的是字符起止，保守用 idx
    return min(idx, len(text))


def parse_cged16_hsk(path):
    import re
    with open(path, encoding="utf-8") as f:
        content = f.read()
    doc_blocks = re.findall(r"<DOC>(.*?)</DOC>", content, re.S)
    out = []
    for block in doc_blocks:
        idm = re.search(r'<TEXT[^>]*>', block)
        text_m = re.search(r"<TEXT[^>]*>(.*?)</TEXT>", block, re.S)
        corr_m = re.search(r"<CORRECTION>(.*?)</CORRECTION>", block, re.S)
        if not text_m or text_m.group(1).strip() == "":
            continue
        tid = idm.group(0) if idm else ""
        tid = tid.replace("<TEXT id=", "").rstrip(">").strip('"')
        raw = text_m.group(1).strip()
        corr = corr_m.group(1).strip() if corr_m else ""

        # 段落内全部 ERROR（CGED 的 start_off/end_off 为 UTF-8 byte 偏移，需转字符索引）
        errs = []
        for em in re.finditer(r'<ERROR start_off="(\d+)" end_off="(\d+)" type="([A-Z])"', block):
            errs.append({
                "start": byte_to_char_idx(raw, int(em.group(1))),
                "end": byte_to_char_idx(raw, int(em.group(2))),
                "type": em.group(3),
            })

        # 切句，并把 ERROR 映射到各自句子（按句子在段落中的绝对起止）
        sents = split_sentences(raw)
        for sent_id, (s_start, sent) in enumerate(sents):
            s_end = s_start + len(sent)  # 句末（含标点）
            s_errs = []
            for e in errs:
                # 偏误点在该句绝对区间内
                lo, hi = e["start"], e["end"]
                if lo >= s_start and hi <= s_end and hi > s_start:
                    s_errs.append({
                        "start": lo - s_start,
                        "end": hi - s_start,
                        "raw_type": e["type"],
                        "ctype": CGED_TYPE_MAP.get(e["type"], e["type"]),
                        "fragment": sent[lo - s_start: hi - s_start],
                    })
            out.append({
                "source": "cged",
                "source_id": f"{tid}#s{sent_id}",
                "raw_sentence": sent.strip(),
                "error_spans": s_errs,
                "correction": corr,  # 段落级修正，可能跨句（识别扩展仅参考）
                "has_error": len(s_errs) > 0,
                "golden_status": "draft",
            })
    return out


def main():
    mu = parse_mucgec(os.path.join(RAW, "MuCGEC_dev.txt"))
    cg = parse_cged16_hsk(os.path.join(RAW, "CGED16_HSK_TrainingSet.txt"))

    retell = {
        "meta": {
            "name": "retell_golden", "version": "0.1",
            "purpose": "复述验证引擎黄金集（MuCGEC 单句+多参考，天然契合单要点复述）",
            "note": "未按 3 型过滤，保留全量便于对标；采样构建时再筛偏误类型",
        },
        "items": mu,
    }
    with open(RET, "w", encoding="utf-8") as f:
        json.dump(retell, f, ensure_ascii=False, indent=2)

    recog = {
        "meta": {
            "name": "cged_recognition", "version": "0.1",
            "purpose": "识别引擎扩展评测集（CGED16 HSK，段落切句 + span 相对句内重算 + 类型映射）",
            "counts": {"总句": len(cg),
                       "带偏误句": sum(1 for it in cg if it["has_error"]),
                       "纯句": sum(1 for it in cg if not it["has_error"])},
        },
        "items": cg,
    }
    with open(CGD, "w", encoding="utf-8") as f:
        json.dump(recog, f, ensure_ascii=False, indent=2)

    print("== 复述黄金集(MuCGEC) ==")
    print(f"  共 {len(mu)} 句")
    print("== CGED 识别扩展 ==")
    print(f"  切句后 {len(cg)} 句 | 带偏误 {sum(1 for it in cg if it['has_error'])} | 纯句 {sum(1 for it in cg if not it['has_error'])}")
    from collections import Counter
    print("  句内span类型分布:", Counter(s["ctype"] for it in cg for s in it["error_spans"]))


if __name__ == "__main__":
    main()
# coding=utf-8
# P0.13: parse hellohejinyu/HSK-3.0 HSK Grammar txt → grammar_examples.json
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYL_PATH = os.path.join(ROOT, "datasets", "syllabus_hsk30_2025.json")
OUT_PATH = os.path.join(ROOT, "datasets", "grammar_examples.json")
TXT_DIR = os.path.join(ROOT, "reports", "_hj_download")

CN_LEVEL = {"1": "一", "2": "二", "3": "三", "4": "四"}


def parse_txt(lv_str):
    level = int(lv_str)
    path = os.path.join(TXT_DIR, f"HSK{lv_str}.txt")
    raw = open(path, encoding="utf-8").read()
    blocks = []
    lines = [_l.rstrip("\n") for _l in raw.splitlines()]
    cur = None  # {"num": int, "name": str, "examples": [str]}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # match 【一XX】语法点名称
        m = re.match(r"^【" + CN_LEVEL[lv_str] + r"(\d{1,2})】(.+)$", line)
        if m:
            if cur is not None:
                blocks.append(cur)
            num = int(m.group(1))
            name = m.group(2).strip()
            cur = {"num": num, "name": name, "examples": []}
            continue
        if cur is not None:
            # 例句必须是带句调标点的完整句（。/？/！）；无标点的行是"词例/列举"，不入库
            if any(c in line for c in ("。", "？", "？", "！", ".", "?")):
                # 去除句末空、保留标点；split 出多个完整分句
                stripped = line.rstrip()
                if not stripped:
                    continue
                # 按句调标点切分为多个完整句
                sents = re.split(r"(?<=[。？!?!])", stripped)
                for s in sents:
                    s = s.strip()
                    if s:
                        cur["examples"].append(s)
            # 无句调标点的整行：忽略
    if cur is not None and cur["examples"]:
        blocks.append(cur)
    return blocks


def main():
    # 1. load syllabus
    with open(SYL_PATH, encoding="utf-8") as f:
        syl = json.load(f)
    syl_points = {}
    for p in syl["points"]:
        m = re.match(r"hsk30-g(\d+)-(\d+)", p["id"])
        if m:
            lvl = int(m.group(1))
            num = int(m.group(2))
            if 1 <= lvl <= 4:
                syl_points[(lvl, num)] = p["id"]
    # 2. parse each txt
    result = {
        "metadata": {
            "source": "hellohejinyu/HSK-3.0 (GitHub)",
            "license_note": "非商业教学用途，保留原作者版权标注",
            "generated_at": "2026-09-10",
            "coverage": {
                "matched": 0,
                "missed": 0,
            }
        },
        "examples": {}  # syllabus_id → [sentence1, sentence2, ...]
    }
    matched = 0
    for lv in ("1", "2", "3", "4"):
        blocks = parse_txt(lv)
        lvi = int(lv)
        for b in blocks:
            key = (lvi, b["num"])
            if key in syl_points:
                sid = syl_points[key]
                # 去重并去空
                exs = [_x.strip() for _x in b["examples"] if _x.strip()]
                exs = list(dict.fromkeys(exs))
                result["examples"][sid] = exs
                matched += 1
    print(f"matched: {matched} 例句")
    # 统计总缺失：level 1-4 共 339 → matched 是命中
    total_l14 = sum(1 for (l, _) in syl_points.keys() if 1 <= l <= 4)
    missed = total_l14 - matched
    result["metadata"]["coverage"]["matched"] = matched
    result["metadata"]["coverage"]["missed"] = missed
    print(f"total l1-4: {total_l14}  matched: {matched}  missed: {missed}")
    # 3. 写盘
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("wrote to", OUT_PATH)


if __name__ == "__main__":
    main()

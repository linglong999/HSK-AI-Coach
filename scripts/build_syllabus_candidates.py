# -*- coding: utf-8 -*-
# ============================================================
# scripts/build_syllabus_candidates.py —— P0.15 两段式入库·第一段
#
# 把 datasets/hsk30_raw/krmanik/ 的 7 个官方 grammar JSON（593 条）
# 合并成 candidates 候选考纲，落盘 datasets/syllabus_hsk30_2025.json，
# status="candidates"（未转正）。人工抽核后置 status="approved" 即转正。
#
# 级别由文件归属决定（字段本身无级别）：HSK 1-6 → int；HSK 7-9 → "7-9"。
# 7-9 合编：level="7-9"、level_gf=None、band="高等"（不强行标单一 GF）。
# id 前缀：hsk30-g{19}-{3位序号}；7-9 用 hsk30-g79-{3位序号}。
# 字段：类别/类别名称/细目 保留原文（细目可为空，id/检索不依赖它）。
#
# 运行: python scripts/build_syllabus_candidates.py
#       [--source DIR] [--out FILE]
# ============================================================

import argparse
import glob
import json
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.syllabus import build_point

RAW_DIR = os.path.join(_PROJECT_ROOT, "datasets", "hsk30_raw", "krmanik")
OUT = os.path.join(_PROJECT_ROOT, "datasets", "syllabus_hsk30_2025.json")

# 文件名 → 级别标签（re 容错空格/间隔符）
_FILE_LEVEL = {
    "HSK 1": 1, "HSK 2": 2, "HSK 3": 3, "HSK 4": 4,
    "HSK 5": 5, "HSK 6": 6, "HSK 7-9": "7-9",
}


def parse_file(stem):
    """从文件名解析级别：'HSK_3' → 3；'HSK_7-9' → '7-9'。未知则抛错。"""
    m = re.fullmatch(r"HSK[ _](\d)(?:-(\d))?", stem)
    if not m:
        raise ValueError("unparseable filename: " + stem)
    lo = m.group(1)
    hi = m.group(2)
    if hi:
        return "7-9"
    return int(lo)


def build(source_dir=RAW_DIR):
    by_level = {}
    points = []
    for path in sorted(glob.glob(os.path.join(source_dir, "*.json"))):
        stem = os.path.splitext(os.path.basename(path))[0]
        try:
            level = parse_file(stem)
        except ValueError as e:
            print("skip:", e)
            continue
        rows = json.load(open(path, encoding="utf-8"))
        # 该级 index 独立自 1 计数（对照官方行序）
        for idx, row in enumerate(rows, 1):
            points.append(build_point(row, level, idx))
        by_level[level] = len(rows)
    return points, by_level


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=RAW_DIR)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    points, by_level = build(args.source)
    out = {
        "version": "krmanik/New HSK (2025) @ 2025",
        "source": "https://github.com/krmanik/HSK-3.0/tree/main/New HSK (2025)/HSK Grammar/json",
        "license": "db0e7effde09bc3b28161836dbde78f62b46fbf5 见上游 License.md (CC BY-SA 4.0)",
        "count": len(points),
        "by_level_domain": {str(k): v for k, v in by_level.items()},
        "points": points,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("合并 %d 条 -> %s" % (len(points), args.out))
    for k in sorted(by_level, key=lambda x: (str(type(x)), str(x))):
        print("  level=%r : %d 条" % (k, by_level[k]))


if __name__ == "__main__":
    main()
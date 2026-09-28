# -*- coding: utf-8 -*-
# ============================================================
# B6 校准 · 对齐缺口诊断（diagnose_calibration.py，只读）
# ------------------------------------------------------------------
# 用途：把 100 条「人类分 / judge focus 分 / 声称质量档」三方合并，
#   精确画出一致性缺口：
#   ① 分档 human vs judge 均值（严格度整体偏移）；
#   ② 显著分歧（|diff|≥2）清单 + 按档/按维分布（哪个段最不一致）；
#   ③ 疑似「样本-档位错放」：人机共识与声称质量档严重冲突
#      （high 却共识≤2 → 疑似高估档；low 却共识≥4 → 疑似低估档）；
#   ④ 维度×档 judge 均值矩阵（揭示 judge 维度间严格度不一致）。
# 只读，不修改 annotation_user.csv / calibration_cases.json。供「处理样本错放
# 与 mid 严格度」前对齐证据用（方案 b）。
# ============================================================
import csv
import json
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
USR_CSV = os.path.join(_HERE, "annotation_user.csv")
CASES_JSON = os.path.join(_HERE, "calibration_cases.json")
JUDGE_FOCUS = os.path.join(_HERE, "judge_verdicts_focus.json")

COL_SCORE = "你的focus分(1-5)"
SPAN = 2          # 显著分歧阈值
CS = 1.75         # 全档一致阈值：共识≤CS 视为"低质"、≥4.25 视为"高质"


def load():
    cases = {c["id"]: c for c in json.load(open(CASES_JSON, encoding="utf-8"))["cases"]}
    judge = json.load(open(JUDGE_FOCUS, encoding="utf-8"))
    human = {}
    with open(USR_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            cid = row["id"].strip()
            raw = (row.get(COL_SCORE) or "").strip()
            human[cid] = int(raw) if raw.isdigit() else None
    # 对齐（只取人类已填 且 三方都有）
    rows = []
    for cid, h in human.items():
        if h is None or cid not in judge or cid not in cases:
            continue
        j = judge[cid]["focus_score"]
        if j is None:
            continue
        c = cases[cid]
        rows.append({"id": cid, "dim": c["dim"], "quality": c["quality"],
                     "human": h, "judge": j, "diff": j - h})
    return rows


def band_stats(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["quality"]].append(r)
    out = {}
    for q, lst in sorted(by.items()):
        h = [r["human"] for r in lst]
        j = [r["judge"] for r in lst]
        out[q] = {"n": len(lst),
                  "human_mean": round(sum(h) / len(h), 2),
                  "judge_mean": round(sum(j) / len(j), 2),
                  "median_human": sorted(h)[len(h) // 2],
                  "median_judge": sorted(j)[len(j) // 2]}
    return out


def disagreements(rows):
    lst = [r for r in rows if abs(r["diff"]) >= SPAN]
    by_band = defaultdict(int)
    by_dim = defaultdict(int)
    for r in lst:
        by_band[r["quality"]] += 1
        by_dim[r["dim"]] += 1
    return lst, dict(by_band), dict(by_dim)


def misplaced(rows):
    """疑似样本-档位错放：人机共识（均值）与声称档冲突。"""
    out = []
    for r in rows:
        cons = (r["human"] + r["judge"]) / 2
        if r["quality"] == "high" and cons <= CS:
            out.append({**r, "consensus": round(cons, 2), "kind": "high档共识却低质"})
        elif r["quality"] == "low" and cons >= 4.25:
            out.append({**r, "consensus": round(cons, 2), "kind": "low档共识却高质"})
    return out


def dim_x_band(rows):
    key = defaultdict(list)
    for r in rows:
        key[(r["dim"], r["quality"])].append(r["judge"])
    return {(d, q): round(sum(v) / len(v), 2) for (d, q), v in sorted(key.items())}


def main():
    rows = load()
    if not rows:
        print("无有效对齐行（可能 human 未填 / judge 缺分）")
        return 1
    print(f"有效对齐行：{len(rows)}/100\n")

    print("=== ① 分档 human vs judge 均值 ===")
    for q, s in band_stats(rows).items():
        print(f"  {q:5} n={s['n']:3} human中位={s['median_human']} "
              f"judge中位={s['median_judge']} | human均={s['human_mean']} "
              f"judge均={s['judge_mean']} 差={round(s['judge_mean']-s['human_mean'],2)}")

    ds, band, dim = disagreements(rows)
    print(f"\n=== ② 显著分歧(≥{SPAN}) {len(ds)}/{len(rows)} 条 · 按档 {band} · 按维 {dim} ===")
    for r in ds[:40]:
        print(f"  {r['id']} dim{r['dim']} {r['quality']}: human={r['human']} "
              f"judge={r['judge']} diff={r['diff']:+d}")

    mp = misplaced(rows)
    print(f"\n=== ③ 疑似样本-档位错放 {len(mp)} 条 ===")
    for r in sorted(mp, key=lambda x: x["consensus"]):
        print(f"  {r['id']} dim{r['dim']} 声称[{r['quality']}] "
              f"human={r['human']} judge={r['judge']} 共识={r['consensus']} <- {r['kind']}")

    print("\n=== ④ 维度×档 judge 均值矩阵（行=维度) ===")
    dims = sorted({r["dim"] for r in rows})
    header = "dim | " + " ".join(f"{q:>5}" for q in ("high", "mid", "low"))
    print("  " + header)
    for d in dims:
        cells = []
        for q in ("high", "mid", "low"):
            cells.append(f"{dim_x_band(rows).get((d, q), '-'):>5}")
        print(f"  {d:>3} | " + " ".join(cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
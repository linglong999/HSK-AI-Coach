# -*- coding: utf-8 -*-
# B6 校准 · 全量样本分类审计（band vs human vs judge）
# 输入：calibration_cases.json(设计档位) + annotation_user.csv(人工) + judge_verdicts_focus.json(judge)
# 输出：逐条 id/dim/band/human/judge/Δ + 三类问题归类：
#   [J 压分] judge 太严：human≥3 ∧ judge≤2（典型 mid→1）
#   [J 抬分] judge 太松：human≤3 ∧ judge≥4
#   [错放]   样本错放：human∧judge 双双显著偏离设计档位（human off-band，非 judge 偏差）
import csv
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(_HERE, "calibration_cases.json")
CSV = os.path.join(_HERE, "annotation_user.csv")
JUDGE = os.path.join(_HERE, "judge_verdicts_focus.json")

BAND = {"high": (4, 5), "mid": (3, 3), "low": (1, 2)}
DIM_NAME = {
    1: "偏误定位", 2: "讲解四段", 3: "复述验证", 4: "介入时机",
    5: "语言分层", 6: "归因谨慎", 7: "任务真实", 8: "角色代入",
    9: "非命令性", 10: "对话推进",
}


def load():
    cases = json.load(open(CASES, encoding="utf-8"))["cases"]
    bands = {c["id"]: (c["dim"], c["quality"]) for c in cases}
    judge = json.load(open(JUDGE, encoding="utf-8"))
    human = {}
    with open(CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            cid = r["id"].strip()
            v = (r.get("你的focus分(1-5)") or "").strip()
            if v:
                human[cid] = int(v)
    return bands, judge, human


def band_mean_off(x, band):
    lo, hi = band
    return x < lo - 0.5 or x > hi + 0.5


def main():
    bands, judge, human = load()
    rows = []
    for cid, (dim, q) in bands.items():
        j = judge.get(cid, {}).get("focus_score")
        h = human.get(cid)
        if h is None or j is None:
            rows.append((cid, dim, q, h, j, []))
            continue
        d = j - h
        tags = []
        if h >= 3 and j <= 2:
            tags.append("J压分")
        elif h <= 3 and j >= 4:
            tags.append("J抬分")
        if band_mean_off(h, BAND[q]) and band_mean_off(j, BAND[q]):
            tags.append("错放")
        rows.append((cid, dim, q, h, j, tags))

    rows.sort(key=lambda r: (r[1], r[0]))
    print(f"{'id':9}{'dim':5}{'band':6}{'h':3}{'j':3}{'Δ':3} 标签")
    for cid, dim, q, h, j, tags in rows:
        hh = "·" if h is None else str(h)
        jj = "·" if j is None else str(j)
        dd = "·" if d is None else "{:+d}".format(0 if j is None or h is None else j - h)
        tag = ",".join(tags) if tags else ""
        print(f"{cid:9}{DIM_NAME.get(dim,'?'):5}{q:6}{hh:3}{jj:3}{dd:3} {tag}")

    print("\n=== 汇总 ===")
    from collections import Counter
    alltags = Counter()
    dimj = Counter()
    dimh = Counter()
    for cid, dim, q, h, j, tags in rows:
        alltags.update(tags)
        if h is not None and j is not None:
            dimj[(dim, "J压分" in tags, "J抬分" in tags)] += 1
    print("问题标签计数:", dict(alltags))

    print("\n=== 按维(J压分/J抬分) ===")
    for dim in range(1, 11):
        n = sum(1 for cid, d, q, h, j, t in rows if d == dim)
        press = sum(1 for cid, d, q, h, j, t in rows if d == dim and "J压分" in t)
        raise_ = sum(1 for cid, d, q, h, j, t in rows if d == dim and "J抬分" in t)
        print(f"  dim{dim:2} {DIM_NAME[dim]:6} n={n:2} J压分={press} J抬分={raise_}")


if __name__ == "__main__":
    main()
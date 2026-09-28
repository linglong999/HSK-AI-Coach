# -*- coding: utf-8 -*-
# B6 校准 · 人工 vs judge 对拍 kappa（纯 python，二次加权 + 线性 + 二元）
import csv
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _marginal(pdf):
    n = sum(sum(row) for row in pdf)
    ri = [sum(row) for row in pdf]
    ci = [sum(pdf[i][j] for i in range(len(pdf))) for j in range(len(pdf[0]))]
    return ri, ci, n


def cohen_linear(pdf):
    n = sum(sum(row) for row in pdf)
    if n == 0:
        return 0.0
    p_obs = sum(pdf[i][i] for i in range(len(pdf))) / n
    ri = [sum(row) for row in pdf]
    ci = [sum(pdf[i][j] for i in range(len(pdf))) for j in range(len(pdf[0]))]
    p_e = sum(ri[i] * ci[i] for i in range(len(pdf))) / (n * n)
    denom = 1 - p_e
    return (p_obs - p_e) / denom if denom else 0.0


def quadratic_w(pdf):
    K = len(pdf)
    n = sum(sum(row) for row in pdf)
    if n == 0:
        return 0.0
    ri = [sum(row) for row in pdf]
    ci = [sum(pdf[i][j] for i in range(K)) for j in range(K)]
    w = [[(i - j) ** 2 / ((K - 1) ** 2) for j in range(K)] for i in range(K)]
    po = sum(pdf[i][j] * (1 - w[i][j]) for i in range(K) for j in range(K)) / n
    peu = sum((ri[i] / n) * (ci[j] / n) * (1 - w[i][j])
              for i in range(K) for j in range(K))
    denom = 1 - peu
    return (po - peu) / denom if denom else 0.0


def binary_k(pdf):
    return cohen_linear(pdf)


def build_pdf(h, f, bins, lo=1.0, hi=5.0, labels=None):
    K = len(bins)
    idx = {l: i for i, l in enumerate(bins)}
    pdf = [[0] * K for _ in range(K)]
    for x, y in zip(h, f):
        pdf[idx[x]][idx[y]] += 1
    return [[float(v) for v in row] for row in pdf]


def main():
    human = {}
    with open(os.path.join(_HERE, "annotation_user.csv"), encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            cid = r["id"].strip()
            v = (r.get("你的focus分(1-5)") or "").strip()
            if v:
                human[cid] = int(v)
    judge = json.load(open(os.path.join(_HERE, "judge_verdicts_focus.json"), encoding="utf-8"))
    h, f = [], []
    for cid in sorted(human):
        if cid in judge and judge[cid]["focus_score"] is not None:
            h.append(human[cid]); f.append(judge[cid]["focus_score"])
    n = len(h)
    print(f"有效对齐 {n}/100")

    grid = build_pdf(h, f, [1, 2, 3, 4, 5])
    kw = quadratic_w(grid)
    print(f"二次加权 kappa (1-5) = {kw:.4f}" + ("  → 达标(≥0.70)" if kw >= 0.70 else "  → 未达标"))

    # 二元化：以 band 判定 pass/fail —— score≥4=pass, ≤3=fail
    bh = [1 if v >= 4 else 0 for v in h]
    bf = [1 if v >= 4 else 0 for v in f]
    bg = build_pdf(bh, bf, [0, 1])
    bk = binary_k(bg)
    agreed = sum(1 for x, y in zip(bh, bf) if x == y)
    print(f"二元 pass/fail 一致率 = {agreed}/{n} ({agreed/n:.1%})  kappa = {bk:.4f}")

    # 分歧分布
    span2 = sum(1 for x, y in zip(h, f) if abs(x - y) >= 2)
    exact = sum(1 for x, y in zip(h, f) if x == y)
    off1 = sum(1 for x, y in zip(h, f) if abs(x - y) == 1)
    print(f"精确一致 {exact} | 差1 {off1} | 差≥2 {span2}")

    # 分档均值
    from collections import defaultdict
    bands = defaultdict(lambda: [[], []])
    cases = {c["id"]: c["quality"] for c in json.load(open(os.path.join(_HERE, "calibration_cases.json"), encoding="utf-8"))["cases"]}
    for cid, hv in human.items():
        if cid in judge and judge[cid]["focus_score"] is not None and cid in cases:
            bands[cases[cid]][0].append(hv); bands[cases[cid]][1].append(judge[cid]["focus_score"])
    for q in ("high", "mid", "low"):
        hh, jj = bands[q]
        if jj:
            print(f"  [{q}] n={len(jj):2} 人工均={sum(hh)/len(hh):.2f}  judge均={sum(jj)/len(jj):.2f}")


if __name__ == "__main__":
    main()
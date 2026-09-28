# -*- coding: utf-8 -*-
# B6 校准 · 生成 calibration_report.json（focus 维人工对拍，含 judge_qualified）
# 从 annotation_user.csv + judge_verdicts_focus.json 实时算 kappa（非硬编码），
# 达标(≥0.70) → judge_qualified=true，作为"启用校准 judge"的落盘记录。
import csv
import datetime
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
KAPPA_THRESHOLD = 0.70


def cohen(ha, ja):
    n = len(ha)
    if n == 0 or len(ja) != n:
        return None
    agreed = sum(1 for x, y in zip(ha, ja) if x == y)
    po = agreed / n
    classes = set(ha) | set(ja)
    pe = sum((sum(1 for x in ha if x == c) / n) * (sum(1 for y in ja if y == c) / n)
             for c in classes)
    denom = 1 - pe
    if denom <= 0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / denom


def quad_kappa(a, b, low=1, high=5):
    n = len(a)
    if n == 0 or len(b) != n:
        return None
    max_w = high - low
    po = sum(1.0 - (abs(x - y) ** 2) / (max_w ** 2) for x, y in zip(a, b)) / n
    ca = {k: a.count(k) for k in range(low, high + 1)}
    cb = {k: b.count(k) for k in range(low, high + 1)}
    pe = sum((ca[i] / n) * (cb[j] / n) * (1.0 - (abs(i - j) ** 2) / (max_w ** 2))
             for i in range(low, high + 1) for j in range(low, high + 1))
    denom = 1 - pe
    if denom <= 0:
        return 1.0 if po >= 0.999 else 0.0
    return (po - pe) / denom


def main():
    human = {}
    with open(os.path.join(_HERE, "annotation_user.csv"), encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            cid = r["id"].strip()
            v = (r.get("你的focus分(1-5)") or "").strip()
            if v:
                human[cid] = int(v)
    judge = json.load(open(os.path.join(_HERE, "judge_verdicts_focus.json"), encoding="utf-8"))
    ids = sorted(human)
    h, j = [], []
    for cid in ids:
        if cid in judge and judge[cid]["focus_score"] is not None:
            h.append(human[cid]); j.append(judge[cid]["focus_score"])
    n = len(h)
    kb = cohen([1 if v >= 4 else 0 for v in h], [1 if v >= 4 else 0 for v in j])
    ko = quad_kappa(h, j)
    report = {
        "name": "hsk_judge_calibration_focus_v2",
        "date": datetime.date.today().isoformat(),
        "n": n,
        "kappa_binary": round(kb, 4),
        "kappa_ordinal": round(ko, 4),
        "threshold": KAPPA_THRESHOLD,
        "judge_qualified": bool(kb is not None and kb >= KAPPA_THRESHOLD and ko is not None),
        "agree_exact": sum(1 for x, y in zip(h, j) if x == y),
        "agree_span2": sum(1 for x, y in zip(h, j) if abs(x - y) >= 2),
        "case_ids": ids,
        "corrections": [
            "CAL-057/058/082（讲解四段/偏误定位）原 mis=mid、人工仅1-2，实为 low → 改档",
            "CAL-096/097/098（归因谨慎）原 mid，文本与 high 锚同档 → 改档 high、人工重评 5/5/4",
        ],
        "notes": [
            "judge 输入形态=独立教学回复文本 focus 维（非完整对话），消除空对话压分偏差",
            "judge prompt 注入每维 1/3/5 描述性锚（已提升进 rubric.md §七），fix 双峰化",
            "已接受残留：8 条命令式口吻 mid（人工3/judge1）系统偏严 + 3 条 dim5 英文占比（人工5/judge3-4）；"
            "不因残留再调锚（防过拟合），记录在此",
        ],
    }
    out = os.path.join(_HERE, "calibration_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"n={n} kappa_binary={report['kappa_binary']} kappa_ordinal={report['kappa_ordinal']} "
          f"judge_qualified={report['judge_qualified']} -> {out}")


if __name__ == "__main__":
    main()
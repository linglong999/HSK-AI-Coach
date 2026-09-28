# -*- coding: utf-8 -*-
# ============================================================
# B6 校准 · focus 维人机对拍（calibrate_focus.py）
# ------------------------------------------------------------------
# 要解决的问题（B6 阻塞项·人工校准 kappa≥0.70 的落地收口）：
#   judge_verdicts.json 中 judge 对纯文本校准条目 verdict 恒为 pass（无红线），
#   二值 kappa 失效；且 calibrate_judge.compute_calibration 的序值分仅扫维度 1–5，
#   与"每条只打 focus 一维（覆盖 1–10）"的口径不匹配（review 结论）。
#   本脚本承接用户填好的 annotation_user.csv，聚合出可直接比对的
#   focus 维 (judge, human) 序值配对，用 quadratic weighted kappa 判人机一致性。
#
# 判分口径（行业规范，见 JUDGE_UPGRADE.md / rubric.md）：
#   - 用 kappa 而非"简单一致率"（chance-corrected，防虚高）；
#   - 序值 1–5 用二次加权（quadratic）：大差异重罚、小差异轻罚；
#   - 阈值 KAPPA_THRESHOLD=0.70：substantial 一档，作为"可上线的生产 judge"
#     门槛（<0.40 视为噪音，<0.70 不驱动真实决策门控）；
#   - 报告不只报单一 kappa：补逐条分岐（|judge-human|≥2）清单与各 focus 维
#     分桶，供人工复核（对齐 Judge's VERDICT 论文的建议）。
# kappa 实现复用 calibrate_judge.quadratic_kappa（纯函数，已由 test_cohen_kappa 对拍锁正确性）。
# 判定依据：classify_kappa(kappa) 输出档位；judge_qualified = quadratic_kappa ≥ 0.70。
# ============================================================
import argparse
import csv
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_EVAL = os.path.dirname(_HERE)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_EVAL))
for _p in (_PROJECT_ROOT, _EVAL, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from calibrate_judge import quadratic_kappa  # noqa: E402  复用已对拍锁定的手写实现

# --- 路径与常量 ------------------------------------------------------
DECIM = 4
KAPPA_THRESHOLD = 0.70          # 与 calibrate_judge.KAPPA_THRESHOLD 一致（substantial 档）
DISAGREE_SPAN = 2               # 人机分差 ≥2 视为"显著分歧"，列入复核清单
CATEGORIES = (1, 2, 3, 4, 5)    # focus 维合法 1–5 整分

USR_CSV = os.path.join(_HERE, "annotation_user.csv")
CASES_JSON = os.path.join(_HERE, "calibration_cases.json")
JUDGE_JSON = os.path.join(_HERE, "judge_verdicts.json")
HUMAN_JSON = os.path.join(_HERE, "human_verdicts.json")
REPORT_JSON = os.path.join(_HERE, "calibration_report_focus.json")

# annotation_user.csv 列位（build_user_sheet.py 产出）
COL_ID = "id"
COL_FOCUS = "focus_dim"
COL_SCORE = "你的focus分(1-5)"
COL_REACH = "你判该样本是否达标(pass/fail)"


def classify_kappa(kappa):
    """kappa 档位解释（Landis & Koch 标准刻度，与调研一致）。
    kappa=None → 无法评估。返回 (档位, 一句话说明)。"""
    if kappa is None:
        return "n/a", "数据不足或全部同分，无法评估"
    if kappa < 0.20:
        return "poor", "一致性差（基本是噪音）"
    if kappa < 0.40:
        return "fair", "一致性尚可，但不可作门控依据"
    if kappa < 0.60:
        return "moderate", "中等一致，未达生产门槛"
    if kappa < 0.80:
        return "substantial", "实质性一致（0.60–0.80：生产 judge 常规目标带）"
    return "almost_perfect", "近乎完美一致（需复核是否过度拟合测试集）"


def parse_user_csv(path):
    """解析用户填好的打分表 → {id: {"focus_dim": int, "focus_score": int?,
    "reach": bool?}}。未填 focus 分的条目保留 focus_dim（供校验），score=None。
    校验：id 唯一、focus_dim 整数、score∈1–5。坏行收集进 errors。"""
    users, errors = {}, []
    seen = {}
    with open(path, encoding="utf-8-sig") as f:
        rows = csv.DictReader(f)
        for row in rows:
            cid = (row.get(COL_ID) or "").strip()
            if not cid:
                continue
            if cid in seen:
                errors.append(f"重复 id: {cid}")
                continue
            seen[cid] = True
            try:
                fd = int((row.get(COL_FOCUS) or "").strip())
            except ValueError:
                errors.append(f"{cid}: focus_dim 非整数 -> {row.get(COL_FOCUS)!r}")
                continue
            raw_score = (row.get(COL_SCORE) or "").strip()
            score = None
            if raw_score:
                try:
                    score = int(raw_score)
                except ValueError:
                    errors.append(f"{cid}: 分数非整数 -> {raw_score!r}")
                    continue
                if score not in CATEGORIES:
                    errors.append(f"{cid}: 分数超出 1-5 -> {score}")
                    continue
            raw_reach = (row.get(COL_REACH) or "").strip().lower()
            reach = None
            if raw_reach:
                reach = raw_reach in ("pass", "通过", "达标", "1", "true", "yes")
                if raw_reach not in ("pass", "fail", "通过", "不通过", "达标",
                                     "不达标", "1", "0", "true", "false", "yes", "no"):
                    reach = raw_reach.startswith(("pass", "达", "1", "true", "yes", "通"))
            users[cid] = {"focus_dim": fd, "focus_score": score, "reach": reach}
    return users, errors


def to_human_verdicts(users, cases_by_id):
    """转出 human_verdicts.json 结构：{id: {focus_dim, focus_score, reach}}。
    仅收录在 cases 中出现的 id；不在此列的 id 记 skips（防打分表与语料漂移）。"""
    out, skips, errors = {}, [], []
    for cid, u in users.items():
        if cid not in cases_by_id:
            skips.append(cid)
            continue
        if u["focus_dim"] != cases_by_id[cid]["dim"]:
            errors.append(f"{cid}: focus_dim {u['focus_dim']} 与语料 {cases_by_id[cid]['dim']} 不一致")
            continue
        out[cid] = {"focus_dim": u["focus_dim"], "focus_score": u["focus_score"],
                    "reach": u["reach"]}
    return out, {"skips": skips, "mismatch": errors, "n_human": len(out)}


def build_pairs(human, judge_verdicts):
    """构造 focus 维配对：(judge_focus[], human_focus[], ids[])，只取已人工评分条目。
    judge 分数来自 judge_verdicts[id].per_dim[str(focus_dim)]（缺失→跳过并记 missing）。"""
    judge_focus, human_focus, ids, missing = [], [], [], []
    for cid, h in human.items():
        if h["focus_score"] is None:
            continue
        pd = (judge_verdicts.get(cid) or {}).get("per_dim") or {}
        jf = pd.get(str(h["focus_dim"]))
        if jf is None:
            missing.append(cid)
            continue
        judge_focus.append(jf)
        human_focus.append(h["focus_score"])
        ids.append(cid)
    return judge_focus, human_focus, ids, missing


def _disagreements(ids, judge_focus, human_focus):
    """|judge−human|≥DISAGREE_SPAN 的条目清单（供人工复核）"""
    return [{"id": cid, "judge": j, "human": h, "span": j - h}
            for cid, j, h in zip(ids, judge_focus, human_focus)
            if abs(j - h) >= DISAGREE_SPAN]


def _per_dim_stats(ids, human, judge_focus, human_focus):
    """按 focus 维分桶：n + 该维 judge/human 均值（看哪几维对齐差）。"""
    from collections import defaultdict
    buckets = defaultdict(list)
    for cid, j, h in zip(ids, judge_focus, human_focus):
        buckets[human[cid]["focus_dim"]].append((j, h))
    return {
        str(d): {"n": len(lst),
                 "judge_mean": round(sum(x[0] for x in lst) / len(lst), DECIM) if lst else None,
                 "human_mean": round(sum(x[1] for x in lst) / len(lst), DECIM) if lst else None}
        for d, lst in sorted(buckets.items())
    }


def calibrate_focus(human, judge_verdicts):
    """核心：构建配对 → quadratic kappa → 结构化报告。
    返回 (report, pairs)。report 含：n/阈值/kappa/档位/判定 + 分歧清单 + 分桶。"""
    jf, hf, ids, missing = build_pairs(human, judge_verdicts)
    n = len(ids)
    kappa = quadratic_kappa(jf, hf) if n >= 2 else None
    grade, note = classify_kappa(kappa)
    report = {
        "n_paired": n,                       # 实际进入对拍的条目数
        "n_annotated_total": sum(1 for h in human.values() if h["focus_score"] is not None),
        "missing_judge": missing,            # judge 缺该维分数的条目（应为空）
        "threshold": KAPPA_THRESHOLD,
        "kappa_focus_quadratic": round(kappa, DECIM) if kappa is not None else None,
        "kappa_grade": grade,
        "kappa_note": note,
        "judge_qualified": kappa is not None and kappa >= KAPPA_THRESHOLD,
        "disagreements_span_ge2": _disagreements(ids, jf, hf),
        "per_dim": _per_dim_stats(ids, human, jf, hf),
    }
    return report, {"ids": ids, "judge": jf, "human": hf}


def _dump_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description="focus 维人机对拍：CSV 人工分 vs judge 分 → quadratic kappa 报告")
    ap.add_argument("--user", default=USR_CSV, help="用户打分的 csv（默认 annotation_user.csv）")
    ap.add_argument("--judge", default=JUDGE_JSON, help="judge 评分 json（judge_verdicts.json）")
    ap.add_argument("--cases", default=CASES_JSON, help="校准语料 json（calibration_cases.json）")
    ap.add_argument("--out-report", default=REPORT_JSON, help="对拍报告落盘路径")
    ap.add_argument("--human-out", default=HUMAN_JSON, help="human_verdicts.json 落盘路径")
    ap.add_argument("--no-write", action="store_true", help="只打印报告，不落盘")
    args = ap.parse_args()

    users, parse_err = parse_user_csv(args.user)
    cases = {c["id"]: c for c in json.load(open(args.cases, encoding="utf-8"))["cases"]}
    judge = json.load(open(args.judge, encoding="utf-8"))
    human, conv = to_human_verdicts(users, cases)

    # 校验汇总：全部错误/跳过如实暴露，不静默吞。
    issues = parse_err + conv["skips"] + conv["mismatch"]
    report, pairs = calibrate_focus(human, judge)
    report["_meta"] = {
        "issues": issues[:50],
        "n_issues": len(issues),
        "n_focus_scores": sum(1 for h in human.values() if h["focus_score"] is not None),
    }

    if not args.no_write:
        _dump_json(args.human_out, human)
        _dump_json(args.out_report, report)

    # 可读摘要（stdout）
    print(f"人工标注: {report['n_annotated_total']} 条 · 对拍配对: {report['n_paired']} 条")
    print(f"focus 维 quadratic kappa = {report['kappa_focus_quadratic']} "
          f"[{report['kappa_grade']}]  threshold={KAPPA_THRESHOLD}  "
          f"qualified={report['judge_qualified']}")
    print(f"  {report['kappa_note']}")
    if report["disagreements_span_ge2"]:
        print(f"显著分歧(分差≥{DISAGREE_SPAN}) {len(report['disagreements_span_ge2'])} 条:")
        for d in report["disagreements_span_ge2"][:15]:
            print(f"    {d['id']}  judge={d['judge']} human={d['human']}")
    if issues:
        print(f"[!] {len(issues)} 条问题（前50）：{issues[:10]} ..." if len(issues) > 10
              else f"[!] 问题：{issues}")
    if not args.no_write:
        print(f"wrote -> {args.human_out}\n      -> {args.out_report}")
    return 0 if report["judge_qualified"] else 1


if __name__ == "__main__":
    sys.exit(main())
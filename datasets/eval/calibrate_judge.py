# -*- coding: utf-8 -*-
# ============================================================
# B6 J3 · judge-human 校准管线（calibrate_judge.py 新版）
# 依 B6-A：①先做 judge-human 校准——100-200 条代表性 HSK 对话人工标注 vs
#   judge 评分，Cohen's kappa ≥0.70 才启用；②报告用 kappa（二值 + ordinal 双口径），
#   禁用"简单一致率"（chance-corrected 易高估、cohort mean 可虚高 ~38.6pp）；
#  ③3+ 人工标注者才切 Krippendorff's alpha。
# kappa 手写实现（~20 行纯函数，非 sklearn）：公式是封闭式算术 (po-pe)/(1-pe)，
#   一个公式引入 numpy/scipy 依赖链不值；用对拍单测锁正确性（拍板点1）。
# 校准集样本 = 合成对话先行（拍板点3：build_cases 锚 case + golden 派生；真实流量
#   出现后再补样重校）。
# ============================================================
import json
import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

KAPPA_THRESHOLD = 0.70          # B6-A 保守门槛


def cohen_kappa(ratings_a, ratings_b):
    """二值/多值 Cohen's kappa：(po - pe) / (1 - pe)。空 → 返回 None。
    po=观察一致率；pe=机会一致率（各类别两标注者边缘概率乘积之和）。"""
    n = len(ratings_a)
    if n == 0 or ratings_b is None or len(ratings_b) != n:
        return None
    classes = set(ratings_a) | set(ratings_b)
    agreed = sum(1 for x, y in zip(ratings_a, ratings_b) if x == y)
    po = agreed / n
    pe = 0.0
    for c in classes:
        pa = sum(1 for x in ratings_a if x == c) / n
        pb = sum(1 for y in ratings_b if y == c) / n
        pe += pa * pb
    denom = 1.0 - pe
    if denom <= 0.0:             # 全同一切类（退化）→ 视为完全一致
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / denom


def quadratic_kappa(a, b, low=1, high=5):
    """序值口径：二次加权 Cohen's kappa（ordinal 1–5 分维）。权 = 1 - 差²/最大差²。"""
    n = len(a)
    if n == 0 or b is None or len(b) != n:
        return None
    ncat = high - low + 1
    max_w = ncat - 1            # 相邻档等差权重（order-preserving）
    # 观察加权一致（混淆矩阵）
    w_obs = 0.0
    for x, y in zip(a, b):
        d = abs(x - y)
        w = 1.0 - (d * d) / (max_w * max_w)   # 二次加权
        w_obs += w
    po = w_obs / n
    # 期望加权一致：边缘概率两两组合
    count_a = {k: a.count(k) for k in range(low, high + 1)}
    count_b = {k: b.count(k) for k in range(low, high + 1)}
    pe = 0.0
    for i in range(low, high + 1):
        for j in range(low, high + 1):
            d = abs(i - j)
            w = 1.0 - (d * d) / (max_w * max_w)
            pe += (count_a.get(i, 0) / n) * (count_b.get(j, 0) / n) * w
    denom = 1.0 - pe
    if denom <= 0.0:
        return 1.0 if po >= 0.999 else 0.0
    return (po - pe) / denom


def sample_cases(cases, n=150, seed=42):
    """从 cases 池分层抽样 n 条（错误类型 × 等级 × 正负锚）近似：
    按 case_type 分桶、桶内随机，尽量覆盖正负与各类型。n>总量则全取。"""
    rng = random.Random(seed)
    buckets = {}
    for c in cases:
        buckets.setdefault(c.get("case_type"), []).append(c)
    pool = []
    for k, v in buckets.items():
        take = max(1, round(n * len(v) / max(1, len(cases)))) if len(cases) else 0
        rng.shuffle(v)
        pool.extend(v[:take])
    pool.sort(key=lambda c: c["id"])
    return pool


def compute_calibration(judge_verdicts, human_verdicts, judge_dims=None,
                        human_dims=None):
    """核心校准统计：二值 kappa + 序值 quadratic kappa（**禁用简单一致率**）。
    返回 dict 报告：判定 judge_qualified = 二值 kappa ≥ 0.70。"""
    kappa_bin = cohen_kappa(judge_verdicts, human_verdicts)
    kappa_ord = None
    if judge_dims is not None and human_dims is not None:
        scores = []
        for jd, hd in zip(judge_dims, human_dims):
            for d in range(1, 6):
                scores.append((jd.get(d), hd.get(d)))
        pairs = [(x, y) for x, y in scores if x is not None and y is not None]
        ja = [x for x, _ in pairs]
        jb = [y for _, y in pairs]
        kappa_ord = quadratic_kappa(ja, jb)
    return {
        "n": len(human_verdicts),
        "kappa_binary": (round(kappa_bin, 4) if kappa_bin is not None else None),
        "kappa_ordinal": (round(kappa_ord, 4) if kappa_ord is not None else None),
        "judge_qualified": kappa_bin is not None and kappa_bin >= KAPPA_THRESHOLD,
        "threshold": KAPPA_THRESHOLD,
        # 只报 kappa，绝不报"简单一致率"——其 chance-corrected 高估风险见模块 docstring
    }


def _dump_report(report, cases):
    cal_dir = os.path.join(_HERE, "calibration")
    os.makedirs(cal_dir, exist_ok=True)
    with open(os.path.join(cal_dir, "calibration_report.json"), "w",
              encoding="utf-8") as f:
        json.dump({**report, "sample_case_ids": [c["id"] for c in cases]},
                  f, ensure_ascii=False, indent=2)
    return cal_dir


def run_calibration(cases, judge_verdicts, human_verdicts,
                    judge_dims=None, human_dims=None, sample=True,
                    n=150, seed=42):
    """校准管线编排（①抽样 → ②③判分对齐 → ④kappa → ⑤≥0.70 输出 report + 落盘。
    judge_verdicts/human_verdicts 为按 case 序对齐的二值列表；dims 为 1-5 分维 dict 列表。
    sample=True 时先抽样（测试可关，直接用给定对）。返回 (report_dict, used_cases)。"""
    used = sample_cases(cases, n=n, seed=seed) if sample else list(cases)
    report = compute_calibration(judge_verdicts, human_verdicts,
                                 judge_dims, human_dims)
    return report, used


if __name__ == "__main__":
    # CLI：注入 judge 评分文件与人工标注文件（TSV：case_id\tverdict\t1..5...）跑校准
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", required=True, help="judge 评分 json（{[id]:{verdict, per_dim}}）")
    ap.add_argument("--human", required=True, help="人工标注 json（{[id]:{verdict, per_dim}}）")
    ap.add_argument("--cases", default=os.path.join(_HERE, "tutor_quality", "cases.json"))
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    with open(args.judge, encoding="utf-8") as f:
        judge_data = json.load(f)
    with open(args.human, encoding="utf-8") as f:
        human_data = json.load(f)
    ids = sorted(set(judge_data) & set(human_data))
    ids = ids[: args.n]
    jv = [judge_data[i]["verdict"] for i in ids]
    hv = [human_data[i]["verdict"] for i in ids]
    jd = [judge_data[i].get("per_dim") for i in ids]
    hd = [human_data[i].get("per_dim") for i in ids]
    report, _ = run_calibration([], jv, hv, jd, hd, sample=False)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["judge_qualified"] else 1)
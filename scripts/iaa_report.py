#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P0.8 · YACLC 标注一致性报告（IAA）

独立分析脚本：不进运行时、不依赖项目零第三方运行时核心。
例外依赖：numpy（仅用于 bootstrap 重采样；无 numpy 时自动退回纯 random 采样）。

产出：
  reports/iaa_yaclc.md            —— 数值 + 样本量 + 过滤口径 + 展开规则 + 诚实解读
  reports/iaa_disagreements.csv   —— 分歧样本清单（标注者判定不一致的句子）

口径（P0.8 已拍板）：
  主口径 = is_grammatical 二分一致性。
  指标    = Fleiss' κ（按标注者数分桶）+ Krippendorff's α（nominal，全量主指标）。
  展开规则 = 聚合计数按 annotator_count 展开成逐标注者判定。
  过滤     = total_annotators < 2 剔除；缺口按缺失处理（不补齐）。
  置信区间 = 对句子层 bootstrap ≥1000 次 → 2.5/97.5 分位。

用法：python scripts/iaa_report.py
"""
import collections
import csv
import json
import os
import random
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "datasets", "_yaclc_valid.jsonl")
OUT_DIR = os.path.join(ROOT, "reports")

N_BOOT = 2000          # bootstrap 重采样次数
SEED = 20260907        # 固定 seed，结果可复跑
MIN_ANNOTATORS = 2     # 过滤：标注者数过小的样本剔除
GOOD = 1               # is_grammatical = 1 的代码取值


def load_rows():
    rows = []
    with open(DATA, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def expand(rows):
    """展开成逐句子标注者判定列表。返回 [(sentence_id, sentence_text, [0/1,...]), ...]"""
    units = []
    for r in rows:
        if r["total_annotators"] < MIN_ANNOTATORS:
            continue  # 过滤（本数据集中实际为 0 条）
        labels = []
        for a in r["sentence_annos"]:
            labels += [int(a["is_grammatical"])] * a["annotator_count"]
        units.append((r["sentence_id"], r["sentence_text"], labels))
    return units


# ---------------- Krippendorff's α（nominal，2 类） ----------------
def krippendorff_alpha(units):
    """按 coincidence matrix 计算 nominal α。容忍每句标注者数不等。"""
    n_codes = 2
    codes = [GOOD, 1 - GOOD]
    O = [[0.0] * n_codes for _ in range(n_codes)]          # coincidence 矩阵（有序对）
    for _, _, labels in units:
        m = len(labels)
        for i in range(m):
            for j in range(m):
                if i != j:
                    ci, cj = labels[i], labels[j]
                    O[ci][cj] += 1.0
    N = sum(sum(row) for row in O)
    if N == 0:
        return float("nan")
    pairs_agree = sum(O[k][k] for k in range(n_codes))
    Do = (N - pairs_agree) / N
    # 期望分歧（nominal）：两随机编码不同的概率
    rowsum = [sum(O[k]) for k in range(n_codes)]
    prob_same = sum((rowsum[k] / N) ** 2 for k in range(n_codes))
    Dc = 1.0 - prob_same
    if Dc == 0:
        return float("nan")
    return 1.0 - Do / Dc


# ---------------- Fleiss' κ ----------------
def _fleiss_kappa_for_bucket(sent_bucket, m):
    """同标注者数 m 的句子子集 → Fleiss' κ（2 类）。"""
    subs = [labels for _, _, labels in sent_bucket if len(labels) == m]
    n = len(subs)
    if n == 0:
        return None
    n0 = n1 = 0          # 各类总计数
    pbar_sum = 0.0
    for labels in subs:
        c0, c1 = labels.count(0), labels.count(1)
        n0 += c0
        n1 += c1
        # 该句内一致对占比（配对数 m*(m-1)）
        agree = c0 * (c0 - 1) + c1 * (c1 - 1)
        total_pair = m * (m - 1)
        pbar_sum += agree / total_pair if total_pair else 0.0
    P = pbar_sum / n
    total = n0 + n1
    p0 = n0 / total
    p1 = n1 / total
    Pe = p0 * p0 + p1 * p1
    if Pe == 1.0:
        return None
    return (P - Pe) / (1 - Pe)


def fleiss_kappa_by_bucket(units):
    """按标注者数分桶报告 Fleiss' κ（表格驱动）。"""
    buckets = bucket_map(units)
    results = []
    for m in sorted(buckets):
        k = _fleiss_kappa_for_bucket(buckets[m], m)
        results.append((m, len(buckets[m]), k))
    return results


def bucket_map(units):
    buckets = collections.defaultdict(list)
    for u in units:
        buckets[len(u[2])].append(u)
    return buckets


# ---------------- bootstrap ----------------
def _boot_alpha(units, n=N_BOOT, seed=SEED):
    """对句子层重采样 → α 的 CI（主指标，容忍变量标注者数）。"""
    rng = random.Random(seed)
    alphas, idx = [], list(range(len(units)))
    for _ in range(n):
        sample = [units[i] for i in (rng.choice(idx) for _ in idx)]
        alphas.append(krippendorff_alpha(sample))
    return _ci([x for x in alphas if x == x])  # 排除 nan


def _boot_kappa_buckets(buckets, n=N_BOOT, seed=SEED):
    """每个标注者数桶内重采样 → κ 的 CI（同桶标注者数恒为 m，合法）。"""
    rng = random.Random(seed)
    out = {}
    for m, sub in buckets.items():
        sub = list(sub)
        if len(sub) < 30:
            out[m] = None          # 样本太少，CI 不稳，只报点估计
            continue
        ys, idx = [], list(range(len(sub)))
        for _ in range(n):
            sample = [sub[i] for i in (rng.choice(idx) for _ in idx)]
            k = _fleiss_kappa_for_bucket(sample, m)
            if k is not None:
                ys.append(k)
        out[m] = _ci(ys) if ys else None
    return out


def _ci(vals):
    if not vals:
        return None
    vals = sorted(vals)
    lo = vals[int(round(0.025 * (len(vals) - 1)))]
    hi = vals[int(round(0.975 * (len(vals) - 1)))]
    return (lo, statistics.mean(vals), hi)


def disagreements(units):
    """labels 内含 0 与 1 的句子。"""
    return [(uid, text, labels) for uid, text, labels in units
            if 0 in labels and 1 in labels]


def descriptive_evidence(rows, units):
    """聚合依赖下真正可支撑的描述性证据：correction 聚合度 + is_grammatical 分歧模式。"""
    n_cand = []                # 每句 distinct correction 候选数
    max_share = []             # 每句最热门单 correction 的 annotator 占比
    n0_dist = []               # 每句 is_grammatical=0 的展开数
    for r in rows:
        n_cand.append(len(r["sentence_annos"]))
        tot = sum(a["annotator_count"] for a in r["sentence_annos"])
        if tot:
            max_share.append(max(a["annotator_count"] for a in r["sentence_annos"]) / tot)
    for _, _, labels in units:
        n0_dist.append(labels.count(0))
    cand = collections.Counter(n_cand)
    share_hist = [0, 0, 0]     # <0.2 / 0.2–0.5 / >0.5 热度分段
    for s in max_share:
        share_hist[0 if s < 0.2 else (1 if s <= 0.5 else 2)] += 1
    n0buckets = collections.Counter(n0_dist)
    exactly_one_0 = sum(1 for x in n0_dist if x == 1)
    return {
        "n_cand_dist": dict(sorted(cand.items())),
        "n_cand_mean": statistics.mean(n_cand),
        "max_share_hist": {"0-20%": share_hist[0], "20-50%": share_hist[1], ">50%": share_hist[2]},
        "n0_dist": dict(sorted(n0buckets.items())),
        "exact_one_0": exactly_one_0,
    }


def write_disagreements_csv(units):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "iaa_disagreements.csv")
    rows = []
    for uid, text, labels in disagreements(units):
        counts = {"0": labels.count(0), "1": labels.count(1)}
        rows.append({"sentence_id": uid, "sentence_text": text,
                     "n_0(不含语法)": counts["0"], "n_1(合语法)": counts["1"]})
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["sentence_id", "sentence_text",
                                          "n_0(不含语法)", "n_1(合语法)"])
        w.writeheader()
        w.writerows(rows)
    return path


def main():
    rows = load_rows()
    units = expand(rows)
    n_raw = len(rows)
    n_used = len(units)

    alpha = krippendorff_alpha(units)
    fleiss_buckets = fleiss_kappa_by_bucket(units)
    alpha_ci = _boot_alpha(units)
    kappa_cis = _boot_kappa_buckets(bucket_map(units))

    ann_dist = collections.Counter(r["total_annotators"] for r in rows)
    exp_counts = collections.Counter(len(labels) for _, _, labels in units)
    n_disagree = len(disagreements(units))
    ev = descriptive_evidence(rows, units)

    # 展开量/总数校验统计
    total_expanded = sum(len(labels) for _, _, labels in units)
    code1 = sum(labels.count(1) for _, _, labels in units)
    mismatch = sum(1 for r, (_, _, lab) in zip(rows, units)
                   if len(lab) != r["total_annotators"])

    os.makedirs(OUT_DIR, exist_ok=True)
    md_path = os.path.join(OUT_DIR, "iaa_yaclc.md")
    csv_path = write_disagreements_csv(units)

    ci_fmt = lambda t: (f"(95% CI {t[0]:.3f}–{t[2]:.3f}, 均值 {t[1]:.3f})"
                        if t else "(CI: 样本过少)")
    bucket_lines = "\n".join(
        f"| {m} | {cnt} | {k:.3f} | {ci_fmt(kappa_cis.get(m))} |"
        if k is not None
        else f"| {m} | {cnt} | — | {ci_fmt(kappa_cis.get(m))} |"
        for m, cnt, k in fleiss_buckets)

    md = f"""# YACLC 标注一致性报告（IAA · Fleiss' κ + Krippendorff's α）

> 生成：`scripts/iaa_report.py`（P0.8）· 数据：`datasets/_yaclc_valid.jsonl`
> seed={SEED}，bootstrap N={N_BOOT} → 结果可复跑。

## 一、样本量与过滤口径

| 项 | 值 |
|---|---|
| 原始句数 | **{n_raw}** |
| 过滤条件 | total_annotators < {MIN_ANNOTATORS} |
| 实际剔除 | {n_raw - n_used} 条（无单标注样本） |
| 参与计算句数 | **{n_used}** |
| 展开后总判定量 | {total_expanded}（合语法=1 → {code1} 条；不含语法=0 → {total_expanded - code1} 条） |
| 分歧句数（0 与 1 并存） | {n_disagree} |

**total_annotators 分布**：{dict(sorted(ann_dist.items()))}

**展开后每句标注者数分布**：{dict(sorted(exp_counts.items()))}

> 展开口径：把 `sentence_annos` 的聚合计数按 `annotator_count` 展开成逐标注者判定
> （同一条 correction 被 n 名标注者产出 → 展开成 n 次同一判定）。
> 展开后的每句标注者数（10–17）与 `total_annotators`（9–11）**全量不一致（{mismatch}/{n_raw} 行）**——
> 本报告以展开后的判定矩阵为准，`total_annotators` 仅作参考（详见「诚实解读」局限）。

## 二、指标结果（先读这里的「有效性判定」）

> **有效性判定：`is_grammatical` 在本聚合数据中不构成逐标注者的句子级二值投票，
> 以下 κ/α 数值是"把 is_grammatical 当投票读"的产物，仅作参考，不代表真实的句子级标注一致性。**
> 依据见下节证据。若要拿到有效的 IAA，需原始非聚合标注（记 backlog）。

### 主指标 · Krippendorff's α（nominal，二分类）

**α = {alpha:.3f}** {ci_fmt(alpha_ci)}

- nominal 口径，容忍每句标注者数不等。**数值不可用于一致性结论**（见下）。

### 参考指标 · Fleiss' κ（按标注者数分桶）

| 标注者数 | 句数 | Fleiss' κ | 95% CI |
|---|---|---|---|
{bucket_lines}

## 三、为什么直接 IAA 不成立（聚合依赖的结构性证据）

| 证据 | 值 | 含义 |
|---|---|---|
| 分歧句（0 与 1 并存） | **{n_disagree} / {n_used}** | 每句都同时含两类 → 非"个别边界句有分歧"，而是固定模式 |
| 每句 is_grammatical=0 展开数分布 | {ev['n0_dist']} | 每句几乎恰好 1–2 个 0 占少数 |
| 恰好仅 1 个 0 的句子占比 | {ev['exact_one_0']} / {n_used} | 少数类近乎"每句固定 1 个" |
| 每句 distinct correction 候选数均值 | {ev['n_cand_mean']:.1f} | 聚合候选多样，但 is_grammatical 不随候选判断 |
| 候选数分布 | {ev['n_cand_dist']} | — |
| 最热门单 correction 热度分段 | {ev['max_share_hist']} | 标注者偏好分散，聚合度普遍不高 |

**推论**：多数 correction 条目带 `is_grammatical=1`、每句固定有约 1 条 `0` 的模式，更接近"correction 候选的合规标记"而非"标注者对原句语法性的独立投票"。因此对 `is_grammatical` 求 κ/α 得到的是团队少数类标记的结构回声（≈0 附近、且因病态常需负值），**不构成句子级标注一致性度量**。

## 四、分歧样本清单

已导出全部句子（结构上均含两类标记）：`reports/iaa_disagreements.csv`（含各档判定计数）。由于上述原因，**此清单不代表"标注歧义句"**，仅作原始数据留档。

## 五、诚实解读

- **对 P0.2 `nature` 标注可信度的启示（定性）**：本聚合数据无法提供 IAA 定量背书。
  其对 `nature` 判定的支撑退化为定性证据——correction 候选多数互不相同（聚合度普遍不高，
  见热度分段），说明标注者间对"怎么改"本身不高度趋同；这对"偏误确实存在"是正向信号，
  对"偏误类别/性质如何定"不提供可信一致。
- **可复现路径**：脚本固定 seed，bootstrap N={N_BOOT}，数值可复跑；但解读结论在本数据内恒定。
- **不声称**：本报告不声称任何一致性达标；不将 α/κ 数值引作质量背书；不静默改判任何标注。
"""
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"[P0.8] 主指标 α = {alpha:.4f} {ci_fmt(alpha_ci)}")
    for m, cnt, k in fleiss_buckets:
        kstr = f"{k:.4f}" if k is not None else "—"
        print(f"[P0.8] Fleiss κ (m={m}, n={cnt}) = {kstr} {ci_fmt(kappa_cis.get(m))}")
    print(f"[P0.8] 分歧句 {n_disagree}; 报告 → {md_path}")
    print(f"[P0.8] 分歧清单 → {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""P0.8 · YACLC 标注一致性报告（Krippendorff's α 双口径 · 2026-09-07 修订版）

独立分析脚本：不进运行时、不依赖项目零第三方运行时核心。

口径（v1 P0.8 修订版，用户实测后拍板）：
  主口径 = α(nominal) on 修正选择   —— 单位=句，取值=correction 字符串，m_u=Σannotator_count
  辅口径 = α(nominal) on 改动幅度   —— edits_count 分桶（0 / 1–2 / 3–4 / 5+）同法
  实测量级（四条硬事实）：
    1. Σannotator_count 与 total_annotators 1000/1000 不吻合（+1~+6；max 组 17 > total 11）
    2. "没改"的标注者大概率不入组（correction==原句 仅 293 句 / 343 票，覆盖不了缺口）
    3. 聚合丢失标注者身份 → Cohen's κ 本就不不可算
    4. ig 语义 = "对原句是否合法"（ig=0 组 edits_count 恒 ≥1，ig=1 组可带 1–7 处润色）
  结论：κ 全部退出交付物；total_annotators 仅存档不参与计算；
        ig 降级为描述统计（加权占比 + ig×edits 交叉核查表）。
  bootstrap 按句重采样（seed 固定）→ 双口径各 95% CI。
  分歧样本清单 = distinct corrections 最多的 top50 句（1000/1000 句都 ≥5 种改法）。

自验关：α 实现先过 2×2 手算样例（全同 → α=1；一同一异 → α=-1/3）再算全量。

产出：
  reports/iaa_yaclc.md            —— 数值（α 双口径 + CI）+ 数据口径 + ig 核查 + 预设解读
  reports/iaa_disagreements.csv   —— top50 分歧句全字段
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

N_BOOT = 2000      # bootstrap 重采样次数
SEED = 20260907    # 固定 seed，可复跑
TOP_N = 50         # 分歧句 top-N

# 改动幅度分桶
EDITS_BUCKETS = [0, 1, 2, 3, 4]     # 桶界: <1 →0, 1-2 →1, 3-4 →2, 5+ →3


def edits_bucket(n):
    if n <= 0:
        return 0
    if n <= 2:
        return 1
    if n <= 4:
        return 2
    return 3


# ---------------- α(nominal)：coincidence 闭合式 ----------------
def krippendorff_nominal(units):
    """Krippendorff's α（nominal）。
    units: [(uid, values:list)]，values 为每名标注者的编码（str/int）。
    闭合式（由 coincidence c_gg=n_g(n_g-1)/(m_u-1)、c_gh=n_g n_h/(m_u-1) 推出）：
      N = Σ m_u
      Do = Σ_u (m_u² - Σ_g n_g²)/(m_u-1) / N
      De = 1 - Σ_c p_c²,  p_c = freq(c)/N
      α = 1 - Do/De
    全同 → α=1；2 标注者 2 句"一同一异" [00][01] → α=-1/3（文献确证；Scott's π 才是 0，此处是 α）。
    """
    N = 0
    do_num = 0.0
    freq = collections.defaultdict(int)
    for _, vals in units:
        m = len(vals)
        N += m
        for v in vals:
            freq[v] += 1
        if m > 1:
            c = collections.Counter(vals)
            do_num += (m * m - sum(v * v for v in c.values())) / (m - 1)
    if N <= 1:
        return float("nan")
    de = 1.0 - sum((v / N) ** 2 for v in freq.values())
    if de <= 0:
        return float("nan")
    return 1.0 - (do_num / N) / de


# ---------------- 数据装载与展开 ----------------
def load_rows():
    rows = []
    with open(DATA, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_units_correction(rows):
    """主口径单位：每句 → 每名标注者的 correction 串（按 annotator_count 展开）。"""
    units = []
    for r in rows:
        vals = []
        for a in r["sentence_annos"]:
            vals += [a["correction"]] * a["annotator_count"]
        units.append((r["sentence_id"], vals))
    return units


def build_units_edits(rows):
    """辅口径单位：每句 → 每名标注者的 edits_count 分桶。"""
    units = []
    for r in rows:
        vals = []
        for a in r["sentence_annos"]:
            vals += [edits_bucket(a["edits_count"])] * a["annotator_count"]
        units.append((r["sentence_id"], vals))
    return units


# ---------------- bootstrap（按句重采样） ----------------
def _boot(units, n=N_BOOT, seed=SEED):
    rng = random.Random(seed)
    idx = list(range(len(units)))
    vals = []
    for _ in range(n):
        sample = [units[i] for i in (rng.choice(idx) for _ in idx)]
        a = krippendorff_nominal(sample)
        vals.append(a)
    return _ci([x for x in vals if x == x])


def _ci(vals):
    if not vals:
        return None
    vals = sorted(vals)
    lo = vals[int(round(0.025 * (len(vals) - 1)))]
    hi = vals[int(round(0.975 * (len(vals) - 1)))]
    return (lo, statistics.mean(vals), hi)


# ---------------- ig 描述统计 + 交叉核查 ----------------
def ig_descriptive(rows):
    total = 0
    ig1 = ig0 = 0
    cross = {}            # bucket -> {ig0, ig1}（按 annotator_count 加权）
    for r in rows:
        for a in r["sentence_annos"]:
            b = edits_bucket(a["edits_count"])
            w = a["annotator_count"]
            total += w
            cell = cross.setdefault(b, {"ig0": 0, "ig1": 0})
            if a["is_grammatical"]:
                ig1 += w
                cell["ig1"] += w
            else:
                ig0 += w
                cell["ig0"] += w
    return {
        "total": total,
        "ig1": ig1, "ig0": ig0,
        "ig1_pct": ig1 / total, "ig0_pct": ig0 / total,
        "cross": cross,
    }


# ---------------- 分歧样本（distinct corrections top50） ----------------
def top_disagreement(rows):
    scored = []
    for r in rows:
        distinct = {a["correction"]: a["annotator_count"] for a in r["sentence_annos"]}
        scored.append((len(distinct), r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:TOP_N]]


def write_disagreements_csv(rows):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "iaa_disagreements.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["sentence_id", "sentence_text", "n_distinct_corrections",
                    "correction", "votes"])
        for r in rows:
            distinct = sorted({a["correction"] for a in r["sentence_annos"]})
            for corr in distinct:
                votes = sum(a["annotator_count"] for a in r["sentence_annos"]
                            if a["correction"] == corr)
                w.writerow([r["sentence_id"], r["sentence_text"],
                            len(distinct), corr, votes])
    return path


# ---------------- 自验关 ----------------
def self_check():
    """2×2 手算样例（2 标注者 × 2 句、共用码类）：
    全同 [0,0][1,1] → α=1；一同一异 [0,0][0,1] → α=-1/3（共用 0 类；Scott's π 才是 0）。"""
    all_same = [("a", [0, 0]), ("b", [1, 1])]
    one_same_one_diff = [("a", [0, 0]), ("b", [0, 1])]
    a1 = krippendorff_nominal(all_same)
    a2 = krippendorff_nominal(one_same_one_diff)
    ok = (abs(a1 - 1.0) < 1e-9) and (abs(a2 + 1.0 / 3.0) < 1e-9)
    return ok, (a1, a2)


def main():
    # 自验关：先过手算样例再算全量
    ok, (a1, a2) = self_check()
    if not ok:
        print("[P0.8] 自验关失败: 全同={} 一同一异={} (期望 1 / -1/3)".format(a1, a2))
        return 1

    rows = load_rows()
    corr_units = build_units_correction(rows)
    edits_units = build_units_edits(rows)

    alpha_corr = krippendorff_nominal(corr_units)
    alpha_edits = krippendorff_nominal(edits_units)
    ci_corr = _boot(corr_units)
    ci_edits = _boot(edits_units)

    ig = ig_descriptive(rows)
    # m_u 与 distinct corrections 分布
    m_u_dist = collections.Counter(len(vals) for _, vals in corr_units)
    distinct_dist = collections.Counter(
        len(set(vals)) for _, vals in corr_units)
    n_dup_groups = sum(1 for _, vals in corr_units if len(set(vals)) < len(vals))
    anno_reuse = {r["sentence_id"]: r["total_annotators"] for r in rows}

    os.makedirs(OUT_DIR, exist_ok=True)
    md_path = os.path.join(OUT_DIR, "iaa_yaclc.md")
    csv_path = write_disagreements_csv(top_disagreement(rows))

    ci_fmt = lambda t: (f"(95% CI {t[0]:.3f}–{t[2]:.3f}, 均值 {t[1]:.3f})"
                        if t else "(CI 不可算)")
    ig_cross_lines = "\n".join(
        f"| {b} | {c['ig0']} | {c['ig1']} | {c['ig0'] + c['ig1']} |"
        for b, c in sorted(ig["cross"].items()))
    distinct_top = distinct_dist.most_common(3)

    md = f"""# YACLC 标注一致性报告（Krippendorff's α 双口径 · 修订版）

> 生成：`scripts/iaa_report.py`（P0.8 修订版）· 数据：`datasets/_yaclc_valid.jsonl`（1000 句）
> seed={SEED}，bootstrap N={N_BOOT}，按句重采样 → 可复跑。

## 〇、数据口径与实测量级（先读这个，才能正确读下面的 α）

**四条硬事实（用户实测 + 本脚本复核）：**

1. **票池不完备**：每句 Σannotator_count 与 total_annotators **1000/1000 不吻合**（差 +1~+6，
   max 组 17 > total 11）→ total_annotators 不是本句票数真值，二元票真分母不可知。
   故：**本报告一律用 m_u = Σ annotator_count，total_annotators 仅存档、不参与任何计算**。
2. **"没改"的标注者大概率不入组**：correction == 原句的组仅 293 句 / 343 票，覆盖不了缺口——
   票池截断方向不可量化，记为局限。
3. **聚合丢失标注者身份** → 需要两两配对的 Cohen's κ **本就不可算**；κ 已全部退出交付物。
4. **ig 语义核查**：ig=0 组 edits_count 恒 ≥1，ig=1 组却可带 1–7 处润色 → ig 是"对原句是否合法"的判断，
   与是否修改无关 → **ig 仅作描述统计，不进任何 IAA**。

**m_u（=Σannotator_count）每句标注者数分布**：{dict(sorted(m_u_dist.items()))}
**每句 distinct corrections 数分布**（top）{[(k, distinct_dist[k]) for k, _ in distinct_top]}：
实际 1000/1000 句 ≥5 种改法 → 分歧清单改为"distinct corrections top{TOP_N}"。

## 一、主口径 · α(nominal) on 修正选择

**α = {alpha_corr:.3f}** {ci_fmt(ci_corr)}

- 单位=句，取值=correction 字符串，m_u=Σannotator_count；coincidence 组级构造
  （c_gg=n_g(n_g−1)/(m_u−1)、c_gh=n_g·n_h/(m_u−1)），α = 1 − D_o/D_e。
- **identity-α 预期很低**：多数句由约 {distinct_dist.most_common(1)[0][0]} 人改出约
  {distinct_dist.most_common(1)[0][0]} 种合法改法。这是**自由改写任务的性质**（每人给一种合法改法），
  **不是标注质量差**，别改数据、别慌。低 α 只说明"标注者对'该怎么改'不强求唯一"，不代表不一致出错。

## 二、辅口径 · α(nominal) on 改动幅度

**α = {alpha_edits:.3f}** {ci_fmt(ci_edits)}

- edits_count 分桶 0 / 1–2 / 3–4 / 5+（桶定义 {EDITS_BUCKETS}）后同法计算。
- 度量"改多狠"的**严重度共识**（教学上对应偏误严重度），不被自由改写的字面低一致拖死。
- 解读：α 越接近 1，说明标注者对"问题有多严重/要动几处"越一致；这是相对可信的共识维度。

## 三、ig 描述统计 + ig×edits 交叉核查（不进 IAA，仅描述）

| 加权标注总数 | ig=0（被判不合法） | ig=1（被判合法） | ig=0 占比 | ig=1 占比 |
|---|---|---|---|---|
| {ig['total']} | {ig['ig0']} | {ig['ig1']} | {ig['ig0_pct']:.1%} | {ig['ig1_pct']:.1%} |

**ig × edits_count 交叉（按 annotator_count 加权）：**

| edits 桶 | ig=0 | ig=1 | 合计 |
|---|---|---|---|
{ig_cross_lines}

> 该表检验"合法性判定"与"改动幅度"关系：若 ig=0 集中在高 edits 桶，说明"被判不合法→改动大"；
> 若分散，则 ig 与幅度相对独立。仅供描述。

## 四、分歧样本清单（distinct corrections top{TOP_N}）

已导出 `reports/iaa_disagreements.csv`：每句 distinct corrections 最多的 {TOP_N} 句，含
sentence_id / 原句 / 去重后修正数 / 各组修正串 / 得票数。供人工复核黄金集（P0.2 `nature` 标注可信度）。

## 五、诚实解读与局限

- **对外口径**：只写"**Krippendorff's α = x.xx，YACLC 1000 句 × 9–11 标注者，修正选择维度**"；
  **不写 κ、不写 ≥0.7**、不把 α 数字当质量达标证据。
- **低 α 的解读预设**：主口径低是自由改写任务特性；辅口径给出相对直接的严重度共识。
- **局限**（不超出数据支持范围）：票池截断方向不可量化；total_annotators 真值不可知；
  ig 仅为描述统计；不对任何标注做静默改判或清洗。

## 六、自验关

α 实现先行通过手算样例：**全同 → α=1（得 {a1:.4f}）；一同一异 → α=-1/3（得 {a2:.4f}）**。
通过后计算的以上全量数值（注：Krippendorff α 的"一同一异"2×2 真值为 −1/3，非 0；0 属 Scott's π）。
"""
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"[P0.8][自验] 全同={a1:.4f} 一同一异={a2:.4f} (期望 1 / -1/3) → 通过")
    print(f"[P0.8] 主口径 α(修正选择) = {alpha_corr:.4f} {ci_fmt(ci_corr)}")
    print(f"[P0.8] 辅口径 α(改动幅度) = {alpha_edits:.4f} {ci_fmt(ci_edits)}")
    print(f"[P0.8] ig 加权占比 1={ig['ig1_pct']:.1%} / 0={ig['ig0_pct']:.1%}")
    print(f"[P0.8] 报告 → {md_path}")
    print(f"[P0.8] 分歧清单(top{TOP_N}) → {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
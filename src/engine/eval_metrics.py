# ============================================================
# engine/eval_metrics.py —— 识别评测「三集拆分 + Wilson 比例区间」
# 纯标准库，零第三方。用于 report 3.1 偏误识别评测的指标时，不再把
# 「种子偏误 / 干净对照 / 对抗」三类样本混成一个点估计，而是分集报告，
# 并对每个比例给 Wilson score 95% 区间；全对/全错时不伪造零宽度区间。
#
# 输入：逐条结果 rows，每条形如
#   {"id": "HSK1-ERR-001", "golden_span": "一个猫"|None, "golden_type": "语法"|None,
#    "status": "TP"|"FN"|"FP"|"TN", "det": "...", "uncertain": 0}
# id 含 "ADV" 判为对抗集；否则 golden_span 有值→种子偏误集、None→干净对照集。
#
# 语义（与 eval/run_eval.py 一致）：
#   种子偏误集  样本 golden 确有偏误，测「命中率 recall」与「类型识别率 type_acc」
#   干净对照集  样本 golden 干净，测「误报率 clean_fp（报了偏误 / 样本数）」
#   对抗集      子集再分：有偏误→命中率；应干净→overcorrection 率
# ============================================================

from math import sqrt
from statistics import NormalDist
from typing import Dict, List, Optional

# 命中判定的子集划分
ADV_TOKEN = "ADV"


def subset_of(row: Dict) -> str:
    """把一条结果归入三集 / 对抗子类。"""
    iid = str(row.get("id", ""))
    if ADV_TOKEN in iid:
        return "adversarial"
    return "error" if row.get("golden_span") else "clean"


def rate_ci(num: int, den: int, *, seed: int = 0, n_boot: int = 2000,
            alpha: float = 0.05) -> Optional[Dict]:
    """比例 num/den 的 Wilson score 区间；seed/n_boot 保留兼容旧调用。

    den<=0 返回 None。区间只量化所给样本的二项比例不确定性；定向构造语料
    不是随机样本，不能把该区间外推到所有学习者或任务。"""
    if den <= 0:
        return None
    if not 0 <= num <= den:
        raise ValueError("rate_ci 计数必须满足 0 <= num <= den")
    if not 0 < alpha < 1:
        raise ValueError("alpha 必须在 (0, 1) 内")
    point = num / den
    z = NormalDist().inv_cdf(1 - alpha / 2)
    z2 = z * z
    denom = 1 + z2 / den
    center = (point + z2 / (2 * den)) / denom
    margin = z * sqrt(point * (1 - point) / den + z2 / (4 * den * den)) / denom
    lo, hi = max(0.0, center - margin), min(1.0, center + margin)
    return {
        "value": round(point, 4),
        "n": den,
        "ci": [round(lo, 4), round(hi, 4)],
        "method": f"Wilson score {round((1 - alpha) * 100, 2):g}%",
    }


def _span_f1(tp, fp, fn) -> Dict:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4)}


def metrics_for_rows(rows: List[Dict], *, subset: str = "error") -> Dict:
    """对一集样本算该集关心的指标 + Wilson 区间。

    种子偏误集 / 对抗中的偏误项：recall + type_acc；
    干净对照集：clean_fp_rate。对抗应干净项：overcorrection 由外层按 status=FP 计数。"""
    result: Dict = {"subset": subset, "n": len(rows)}
    if subset in ("error", "adversarial"):
        hits = [r for r in rows if r.get("status") == "TP"]
        nhit = len(hits)
        nerr = len(rows)
        result["recall"] = rate_ci(nhit, nerr, seed=ord(subset[0]))
        # 类型识别率：仅在有命中的偏误样本内比较（与 eval/run_eval.py 同口径）
        t_correct = sum(1 for r in hits if _det_type_of(r)
                        and r.get("golden_type") == _det_type_of(r))
        result["type_acc"] = rate_ci(t_correct, nhit, seed=ord(subset[0]) + 1)
    elif subset == "clean":
        flagged = sum(1 for r in rows if r.get("status") in ("FP", "TP"))
        result["clean_fp_rate"] = rate_ci(flagged, len(rows), seed=998)
    return result


def _det_type_of(row: Dict) -> Optional[str]:
    """从 det 字段（形如 "一个猫(语法)"）析出识别出的类型，命中行用于 type 比对。"""
    det = str(row.get("det", ""))
    if "(" not in det:
        return None
    inner = det.rsplit("(", 1)[1].rstrip(")")
    return inner if inner else None


def report(rows: List[Dict], *, n_boot: int = 2000, alpha: float = 0.05) -> Dict:
    """三集拆开的透明指标报告。

    返回结构（每集给点估计 + Wilson 区间；无该集数据如实报缺）：
      auto_note   口径与局限
      subsets: {
        error:       {n, recall{value,ci}, type_acc{value,ci}}
        clean:       {n, clean_fp_rate{value,ci}}
        adversarial: {n, error_ratio, overcorrection_rate{value,ci}, recall_on_error...}
      }
      pooled: 原 run.py 的整体值（保留对照，非主结论）
    """
    auto_note = (
        "指标按三集拆分报告，不再与干净/对抗混成一个点估计；每个比例给 Wilson "
        "score 95% 区间。样本为定向构造，区间不可外推总体；样本极小时避免下强结论。"
    )
    err_rows = [r for r in rows if subset_of(r) == "error"]
    clean_rows = [r for r in rows if subset_of(r) == "clean"]
    adv_rows = [r for r in rows if subset_of(r) == "adversarial"]

    # 对抗集再按「有偏误(应命中)」/「应干净(测 overcorrection)」分开
    adv_err = [r for r in adv_rows if r.get("golden_span")]
    adv_clean = [r for r in adv_rows if not r.get("golden_span")]
    adv_over_den = len(adv_clean)
    adv_over = sum(1 for r in adv_clean if r.get("status") == "FP")
    adv_sub = {
        "n": len(adv_rows),
        "error_ratio": round(len(adv_err) / len(adv_rows), 4) if adv_rows else None,
        "overcorrection_rate": rate_ci(adv_over, adv_over_den, seed=555),
    }
    if adv_err:
        adv_err_hit = sum(1 for r in adv_err if r.get("status") == "TP")
        adv_sub["recall_on_error"] = rate_ci(adv_err_hit, len(adv_err), seed=556)

    # pooled（原全局口径，保留作对照，非主结论）
    tp = sum(1 for r in rows if r.get("status") == "TP")
    fp = sum(1 for r in rows if r.get("status") == "FP")
    fn = sum(1 for r in rows if r.get("status") == "FN")
    pooled = _span_f1(tp, fp, fn)

    return {
        "auto_note": auto_note,
        "subsets": {
            "error": metrics_for_rows(err_rows, subset="error"),
            "clean": metrics_for_rows(clean_rows, subset="clean"),
            "adversarial": adv_sub,
        },
        "pooled": pooled,
        "generated_at": __import__("time").time(),
    }

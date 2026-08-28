# ============================================================
# 3.3 复述验证引擎评测
# 输入：datasets/eval/retell_verification.json (v0.3, 60 条)
#       方向一(构造) + 方向二(MuCGEC 真实句→评测样本)
# 指标：
#   - 判定一致性(整体一致率 + 混淆矩阵 pass/partial/fail/hollow)
#   - 通过准确率(truth=pass → verifier 判 pass 的比例)
#   - 流利空洞拦截率(truth=hollow → verifier 判 fail 或 flag_flowery 的比例)
#   - 方向二多 pass 处理：iso_solution>1 的等价解仍应判 pass，计入"通过准确率(等价包容)"
# 运行：
#   python run_eval_3_3.py                  # 全量 60 条
#   python run_eval_3_3.py --limit 3        # dry-run
# 依赖：DEEPSEEK_API_KEY（.env 或环境变量）
# ============================================================

import os
import sys
import json
import argparse

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT_ROOT)

from engine.verifier import Verifier
from engine.llm.client import LLMClient


# ---------- 指标计算 ----------
# 判定一致性：verdict 与 golden truth 的映射
# golden 的 partial/hollow 是"语义需二语仲裁"的样本 → 与 verifier 的"覆盖度带"不对等。
# 我们把"直接可判"的 pass/fail 作为硬一致性；partial/hollow 作为软指标(见下)。
TRUTH_TO_ACCEPT = {
    # golden truth -> 可接受的 verifier verdict 集合
    "pass":    {"pass"},                               # 我们只认它判 pass（通过准确率）
    "fail":    {"fail"},                               # fail 应判 fail
    "partial": {"partial", "fail"},                    # 部分 → 至少非 pass（边界偏 fail 可接受）
    "hollow":  {"fail"},                               # 空洞 → 必须 fail 才能算拦截
}


def normalize_verdict(v):
    return (v or "").lower()


def run(limit=None, out_path=None):
    ds_path = os.path.join(_PROJECT_ROOT, "datasets", "eval", "retell_verification.json")
    with open(ds_path, encoding="utf-8") as f:
        ds = json.load(f)

    items = ds["items"]
    if limit and limit < len(items):
        items = items[:limit]

    verifier = Verifier(client=LLMClient())
    print("=" * 66)
    print("3.3 复述验证引擎评测（retell_verification v0.3）")
    print(f"样本：{len(items)} 条（方向一{sum(1 for i in items if i['source']=='direction1_constructed')} + "
          f"方向二{sum(1 for i in items if i['source']=='direction2_mucgec')}）")
    print("=" * 66)

    rows = []
    # 累计
    n_pass_true = n_pass_hit = 0
    n_hollow_true = n_hollow_block = 0
    conf = {}   # 混淆矩阵 truth -> verdict 计数
    cons_hard_hits = cons_hard_total = 0
    degraded = 0
    flowery_flag_hits = 0

    for it in items:
        iid = it["id"]
        truth = it["truth"]
        kps = it["key_points"]
        kps_clean = [{"id": kp["id"], "text": kp["text"]} for kp in kps]

        try:
            res = verifier.verify(
                explanation=it["explanation"],
                key_points=kps_clean,
                restatement=it["restatement"],
                commit_graph=False,          # 评测模式：不回写图谱
                bias_ref=None,
            )
        except Exception as e:
            rows.append({"id": iid, "truth": truth, "status": "FAIL", "error": repr(e)})
            print(f"[FAIL] {iid}: {e}")
            continue

        verdict = normalize_verdict(res["verdict"])
        is_degraded = res.get("degraded", False)
        flowery = res.get("flowery_but_empty", False)
        if is_degraded:
            degraded += 1
        if flowery:
            flowery_flag_hits += 1

        conf.setdefault(truth, {})
        conf[truth][verdict] = conf[truth].get(verdict, 0) + 1

        # 硬一致性（pass/fail 直接可判）
        if truth in ("pass", "fail"):
            cons_hard_total += 1
            if verdict == truth:
                cons_hard_hits += 1

        # 通过准确率
        if truth == "pass":
            n_pass_true += 1
            if verdict == "pass":
                n_pass_hit += 1

        # 流利空洞拦截率（truth=hollow → 判 fail 即拦截）
        if truth == "hollow":
            n_hollow_true += 1
            if verdict == "fail":
                n_hollow_block += 1

        # 部分正确样本：非 pass 即可接受（边界）
        rows.append({
            "id": iid,
            "source": it["source"],
            "source_id": it.get("source_id"),
            "iso_solution": it.get("iso_solution", 1),
            "kp_id": it.get("kp_id"),
            "truth": truth,
            "restatement": it["restatement"],
            "explanation": it["explanation"],
            "key_points": kps_clean,
            "verdict": verdict,
            "flowery_but_empty": flowery,
            "covered_points": res.get("covered_points"),
            "total_points": res.get("total_points"),
            "coverage_ratio": res.get("coverage_ratio"),
            "point_judgements": res.get("point_judgements", []),
            "degraded": is_degraded,
            "match": (verdict == truth),
        })
        print(f"[OK] {iid} | truth={truth:<8} verdict={verdict:<8} "
              f"cov={res.get('coverage_ratio')} flowery={flowery}")

    # 汇总
    print("\n" + "=" * 66)
    print("3.3 指标汇总")
    print("=" * 66)
    total_ok = len(rows)
    print(f"调用成功: {total_ok}/{len(items)}（降级={degraded}）")
    if cons_hard_total:
        print(f"判定一致性(pass/fail 硬一致性): {cons_hard_hits}/{cons_hard_total} = {cons_hard_hits/cons_hard_total:.2%}")
    if n_pass_true:
        print(f"通过准确率(truth=pass → pass): {n_pass_hit}/{n_pass_true} = {n_pass_hit/n_pass_true:.2%}")
    if n_hollow_true:
        print(f"流利空洞拦截率(truth=hollow → fail): {n_hollow_block}/{n_hollow_true} = {n_hollow_block/n_hollow_true:.2%}")
    print(f"flag_flowery_but_empty 打点次数: {flowery_flag_hits}")
    if conf:
        print("\n混淆矩阵(truth → verdict 计数):")
        for t, vc in conf.items():
            print(f"  {t:<8}: " + ", ".join(f"{v}={c}" for v, c in sorted(vc.items())))

    out_path = out_path or os.path.join(_PROJECT_ROOT, "datasets", "eval", "eval_3_3_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "meta": {"name": "3.3 复述验证评测", "version": "0.3",
                     "note": "verifier 判定一致性的细化；partial/hollow 为语义仲裁样本"},
            "metrics": {
                "total": len(items), "success": total_ok, "degraded": degraded,
                "consistency_hard": {"hits": cons_hard_hits, "total": cons_hard_total,
                                     "rate": (cons_hard_hits / cons_hard_total) if cons_hard_total else None},
                "pass_accuracy": {"hits": n_pass_hit, "total": n_pass_true,
                                  "rate": (n_pass_hit / n_pass_true) if n_pass_true else None},
                "flowery_block_rate": {"hits": n_hollow_block, "total": n_hollow_true,
                                       "rate": (n_hollow_block / n_hollow_true) if n_hollow_true else None},
                "flowery_flag_count": flowery_flag_hits,
                "confusion": conf,
            },
            "rows": rows,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n结果已存: {out_path}（含逐条 verdict + point_judgements）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="只评测前 N 条（dry-run）")
    args = ap.parse_args()
    run(limit=args.limit)
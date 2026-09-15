# -*- coding: utf-8 -*-
# scripts/report_eval_metrics.py —— 对已持久化的识别评测结果做「三集拆分 + bootstrap CI」透明度报告
# 输入：datasets/eval/eval_results.json（eval/run_eval.py 的产物，含逐条 rows）
# 输出：控制台打印 + 写 datasets/eval/eval_transparency.md
# 运行：python scripts/report_eval_metrics.py [--n-boot 2000]
# 说明：不需要 LLM Key，纯离线重算，业务逻辑见 engine/eval_metrics.py。
import argparse
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from engine.eval_metrics import report


def _fmt(stat, fmt=".2f"):
    """把 {value,ci} 渲染成 `0.00 [0.00, 0.00] (n=xx)`，缺数据渲染为"—"。"""
    if stat is None:
        return "—（无数据，如实报缺）"
    ci = stat.get("ci")
    cistr = (f"[{ci[0]:{fmt}}, {ci[1]:{fmt}}]"
             if ci else "无CI")
    return f"{stat['value']:{fmt}} {cistr} (n={stat['n']})"


def main():
    ap = argparse.ArgumentParser(description="识别评测三集拆分 + bootstrap CI 透明度报告")
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--json", default=os.path.join(
        _PROJECT_ROOT, "datasets", "eval", "eval_results.json"))
    ap.add_argument("--out", default=os.path.join(
        _PROJECT_ROOT, "datasets", "eval", "eval_transparency.md"))
    args = ap.parse_args()

    with open(args.json, encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("rows", [])
    rep = report(rows, n_boot=args.n_boot)
    subs = rep["subsets"]

    lines = ["# 识别评测 · 三集拆分指标与置信区间（透明度报告）",
             "",
             f"> 数据：`{os.path.relpath(args.json, _PROJECT_ROOT)}`（version "
             f"{data.get('version', '?')}），逐条 {len(rows)} 项，离线重算（不调用 LLM）。",
             f"> 方法：{rep['auto_note']}",
             "",
             "| 集 | 样本数 | 命中率 recall | 类型识别率 type_acc | 误报/过纠率 |",
             "|---|:--:|:--:|:--:|:--:|",
             f"| 种子偏误集 | {subs['error']['n']} | "
             f"{_fmt(subs['error'].get('recall'))} | {_fmt(subs['error'].get('type_acc'))} | — |",
             f"| 干净对照集 | {subs['clean']['n']} | — | — | "
             f"{_fmt(subs['clean'].get('clean_fp_rate'))} |",
             "| 对抗·偏误项 | — | "
             f"{_fmt(subs['adversarial'].get('recall_on_error'))} | — | — |",
             "| 对抗·应干净项(overcorrection) | — | — | — | "
             f"{_fmt(subs['adversarial'].get('overcorrection_rate'))} |",
             "",
             "## 对照：原整体口径（F1 混池，非主结论）",
             "",
             f"- 精确率 {rep['pooled']['precision']:.3f} · 召回率 "
             f"{rep['pooled']['recall']:.3f} · F1 {rep['pooled']['f1']:.3f}",
             "",
             "> 说明：混池 F1 会把「干净误报」与「对抗过纠」折进检测分，掩盖分集差异；"
             "分集报告以消除该混叠。样本量小时 CI 偏宽属正常，勿据单一数字下强结论。",
             "",
             "## 逐集细项（与 engine/eval_metrics.py 同口径）",
             "",
             "```json",
             json.dumps(rep["subsets"], ensure_ascii=False, indent=2),
             "```",
             ""]

    md = "\n".join(lines)
    print(md)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"\n[已写入] {args.out}")


if __name__ == "__main__":
    main()
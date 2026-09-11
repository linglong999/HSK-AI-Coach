# ============================================================
# tutor_quality · 评测驱动器
# 读 cases.json → 逐条跑 judge（L1 断言 / L3 红线；L2 占位）→ 汇总 → 版本化报告
# 用法:
#   python -m datasets.eval.tutor_quality.run_tutor_judge            # 全跑
#   python -m datasets.eval.tutor_quality.run_tutor_judge --case TQ-HSK1ERR002
# 回归门槛：红线任一再触发 | overall fail；pass 率 < 90% 门禁失败。
# 首版——无 LLM-judge，全确定性（L1+L3）。L2 接入见 rubric.md 四节。
# ============================================================
import os
import sys
import json
import time
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from judge import load_cases, judge_case  # noqa: E402
from engine.router import Router  # noqa: E402

PASS_THRESHOLD = 0.90


def summarize(results):
    n = len(results)
    fail_ids = [r.case["id"] for r in results if r.redline_fails]
    pending_ids = [r.case["id"] for r in results if r.pending]
    redlines_seen = {}
    for r in results:
        for rl in r.redline_fails:
            redlines_seen[rl] = redlines_seen.get(rl, 0) + 1
    pass_n = n - len(fail_ids)
    rate = (pass_n / n) if n else 0.0
    return {"total": n, "passed": pass_n, "pass_rate": round(rate, 3),
            "gate": rate >= PASS_THRESHOLD and not fail_ids,
            "fail_ids": fail_ids, "pending_ids": pending_ids,
            "redline_counts": redlines_seen}


def write_report(results, meta):
    s = summarize(results)
    date = time.strftime("%Y-%m-%d")
    lines = [f"# tutor_quality 评测报告（{date}）", "",
             f"- 用例数：{s['total']} · 确定通过：{s['passed']} · 通过率：{s['pass_rate']}",
             f"- 门禁（≥90% 且无确定红线）：**{'PASS' if s['gate'] else 'FAIL'}**", "",
             "## 确定红线触发（FAIL）", ""]
    if s["redline_counts"]:
        lines += [f"- {k}: {v}" for k, v in s["redline_counts"].items()]
    else:
        lines.append("- 无")
    if s["pending_ids"]:
        lines += ["", "## 待复核（PENDING，不计 fail）", f"- {', '.join(s['pending_ids'])}"]
    lines += ["", "## 逐 case 详情",
              "| id | type | L1命中 | redlines | L2 | elapsed_ms |",
              "|---|---|---|---|---|---|"]
    for r in results:
        l1 = r.l1 or {}
        l2 = (r.l2 or {}).get("verdict")
        l2c = l2 if l2 else "--"
        if not meta.get("mock_llm") and (r.l2 or {}).get("skipped"):
            l2c = "skipped"
        lines.append(f"| {r.case['id']} | {r.case['case_type']} | {l1.get('matched','-')} "
                     f"| {','.join(r.redline_fails) or '-'} | {l2c} "
                     f"| {(r.trace or {}).get('elapsed_ms','-')} |")
    lines += ["", "## 版本", ""]
    mock_note = "（--mock-llm 规则化占位，非真实 LLM-judge）" if meta.get("mock_llm") else ""
    lines += [f"- 模型：{meta.get('model','(默认)')}", f"- case 版本：v0.1",
              f"- LLM-judge：{'MOCK' + mock_note if meta.get('mock_llm') else '未接入（L2 skipped）'}",
              f"- 日期：{time.strftime('%Y-%m-%d %H:%M:%S')}",
              "- 说明：红线=确定判分；L2 接入真实 LLM-judge 后需按 rubric.md §四 人工校准"]
    report_dir = os.path.join(_PROJECT_ROOT, "reports")
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir, f"eval_tutor_quality_{date}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="", help="单 case id（默认全跑）")
    ap.add_argument("--model", default="", help="被测模型（记录用，接真实 LLM-judge 时填）")
    ap.add_argument("--mock-llm", action="store_true",
                    help="用规则化 L2 判分验证管线（不花 Key，符合无 Key 也能跑）")
    args = ap.parse_args()

    cases = load_cases()
    selected = cases if not args.case else [c for c in cases if c["id"] == args.case]
    if args.case and not selected:
        print("case not found:", args.case)
        return 1

    router = Router()
    results = []
    for case in selected:
        results.append(judge_case(case, router, mock_llm=args.mock_llm))
        l2tag = "mock" if args.mock_llm else (
            "skipped" if results[-1].l2 and results[-1].l2.get("skipped") else "-")
        print(f"[{case['id']}] type={case['case_type']} fail={results[-1].redline_fails} "
              f"pending={len(results[-1].pending)} l2={l2tag}", flush=True)

    path = write_report(results, {"model": args.model, "mock_llm": args.mock_llm})
    s = summarize(results)
    print("\n== summary ==")
    print(f"total={s['total']} passed={s['passed']} rate={s['pass_rate']} gate={'PASS' if s['gate'] else 'FAIL'}")
    if s["fail_ids"]:
        print("fail_ids:", s["fail_ids"])
    print("report ->", path)
    return 0 if s["gate"] else 2


if __name__ == "__main__":
    sys.exit(main())
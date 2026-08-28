# ============================================================
# 3.5 文件讲解效果评测
# 测两路分流质量（对齐 2.5/2.6 数据通道）：
#  - 偏误选中片段 → 纠错链（识别到偏误 → 讲解 → 图谱沉淀）
#  - 正确选中片段（含 3.1 clean 对抗 + 地道短语）→ 泛讲解（0 误判为偏误）
# 自动指标 + 人工抽评清单
# 用法: python -m datasets.eval.eval_3_5 [--limit N] [--correct-limit M] [--from-results]
#   --from-results: 从已保存的 eval_3_5_results.json 仅重出清单（改口径零重跑，不调用模型）
# ============================================================

import os
import sys
import json
import argparse

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.file_mode import FileCoach

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden_v1_4.json")

# 正确地道短语（泛讲解样本，覆盖"读懂用法/习语"场景）
PHRASES = [
    "谢谢你的好意",
    "这笔买卖很划算",
    "你说得在理",
    "我们一拍即合",
    "这不是小事",
]


def load():
    d = json.load(open(GOLDEN, encoding="utf-8"))
    bias, clean = [], []
    for grp in ("seed_golden", "adversarial"):
        for e in d.get(grp, []):
            if e.get("error_type") or e.get("error_span"):
                bias.append(e)
            else:
                clean.append({"id": e.get("item_id"), "text": e.get("original", "")})
    return bias, [c for c in clean if c["text"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="偏误样本数(0=全量)")
    ap.add_argument("--correct-limit", type=int, default=0, help="正确样本数(0=全量)")
    ap.add_argument("--from-results", action="store_true",
                    help="从已保存的 eval_3_5_results.json 仅重出清单，不调用模型")
    args = ap.parse_args()

    if args.from_results:
        p = os.path.dirname(os.path.abspath(__file__))
        rd = json.load(open(os.path.join(p, "eval_3_5_results.json"), encoding="utf-8"))
        m = compute_metrics(rd["bias"], rd["correct"])
        write_checklist(os.path.join(p, "3.5人工抽评清单.md"), m, rd["bias"], rd["correct"])
        print(json.dumps(m, ensure_ascii=False, indent=2))
        return

    bias, clean = load()
    correct = [{"id": f"PHRASE-{i+1}", "text": p, "phrase": 1} for i, p in enumerate(PHRASES)]
    correct += [dict(c, phrase=0) for c in clean]

    if args.limit:
        bias = bias[: args.limit]
    if args.correct_limit:
        correct = [c for c in correct if c.get("phrase")][: args.correct_limit] \
            if False else correct[: args.correct_limit]

    coach = FileCoach(learner_id="eval_3_5", native_lang="英语", user_level="HSK3")

    bias_rows, correct_rows = [], []
    bias_errors_ingested = 0

    for e in bias:
        text = e.get("original", "")
        try:
            r = coach.explain_selection(text, event_key=f'3.5-bias-{e["item_id"]}')
        except Exception as ex:
            bias_rows.append({"id": e["item_id"], "text": text, "error": str(ex)})
            continue
        errs = r.get("errors", [])
        bias_errors_ingested += bool(errs)
        span_exact = span_cover = 0
        frags, expl_full = [], []
        gs = e.get("error_span", "")
        for er in errs:
            frag = (er.get("error") or {}).get("fragment", "")
            if frag:
                frags.append(frag)
                if gs:
                    if frag == gs:
                        span_exact = 1
                    if gs in frag:
                        span_cover = 1
            ex = (((er.get("explanation") or {}).get("explanation")) or "").strip()
            if ex:
                expl_full.append(ex)
        bias_rows.append({
            "id": e["item_id"], "text": text, "golden_span": gs,
            "split_ok": bool(errs), "span_exact": span_exact, "span_cover": span_cover,
            "fragment": ";".join(frags),
            "expl_full": "|||".join(expl_full),
        })

    for c in correct:
        text = c["text"]
        try:
            r = coach.explain_selection(text, event_key=f'3.5-correct-{c["id"]}')
        except Exception as ex:
            correct_rows.append({"id": c["id"], "text": text, "error": str(ex)})
            continue
        p = r.get("plain") or {}
        kp_n = len(p.get("key_points", []))
        correct_rows.append({
            "id": c["id"], "text": text, "phrase": c.get("phrase", 0),
            "no_false_pos": not r.get("has_error"),
            "plain_ok": bool(p.get("explanation")) and bool(p.get("key_points")),
            "expl_full": (p.get("explanation") or "").strip(),
            "kp_n": kp_n,
            "kp_ok": kp_n >= 3,   # P2：泛讲解要点数下限（≥3）
        })

    metrics = compute_metrics(bias_rows, correct_rows)
    out = {
        "meta": {"name": "eval_3_5_file_mode", "version": "0.2",
                 "note": "3.5 文件讲解两路分流评测；含 P2 要点数≥3 口径"},
        "metrics": metrics, "bias": bias_rows, "correct": correct_rows,
        "graph_queue": [{"kp_id": q.get("kp_id"), "err": (q.get("node") or {}).get("error_count")}
                        for q in coach.review_queue()],
        "bias_errors_ingested": bias_errors_ingested,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_3_5_results.json")
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    md_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "3.5人工抽评清单.md")
    write_checklist(md_path, metrics, bias_rows, correct_rows)

    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print("图谱复习队列 kp 数:", len(out["graph_queue"]), "| 偏误沉淀:", bias_errors_ingested)


def compute_metrics(bias_rows, correct_rows):
    """自动指标（含 P2 要点数≥3 下限）。口径见 write_checklist 的"指标口径"段。"""
    def pct(x, n):
        return round(x / n * 100, 1) if n else 0.0

    n_b = len(bias_rows)
    n_c = len(correct_rows)
    bi = [r for r in bias_rows if r.get("error") is None]
    co = [r for r in correct_rows if r.get("error") is None]
    return {
        "样本": {"偏误句": n_b, "正确片段": n_c},
        "偏误分流正确率": pct(sum(1 for r in bi if r["split_ok"]), len(bi)),
        "偏误span精确命中率": pct(sum(1 for r in bi if r["span_exact"]), len(bi)),
        "偏误span包含率": pct(sum(1 for r in bi if r["span_cover"]), len(bi)),
        "正确句误报率": pct(sum(1 for r in co if not r["no_false_pos"]), len(co)),
        "泛讲解结构合规率": pct(sum(1 for r in co if r["plain_ok"]), len(co)),
        "泛讲解要点数下限(≥3)达标率": pct(
            sum(1 for r in co if r.get("kp_ok", r.get("kp_n", 0) >= 3)), len(co)),
    }


def write_checklist(path, metrics, bias_rows, correct_rows):
    """生成人工抽评清单 md。说明：完整讲解段不定长输出（人工评审版），表格段仅做摘要。
    含"指标口径与金标标注口径"说明，避免边界句误读。"""

    def esc(s, n=0):
        s = (s or "").replace("|", "\\|").replace("\n", " ")
        s = s.rstrip(" ")
        return s if n <= 0 else s[:n]

    n_b = len(bias_rows)
    n_c = len(correct_rows)
    md = ["# 3.5 文件讲解效果 · 人工抽评清单（" + str(n_b + n_c) + " 条）", "",
          "## 自动指标", "", "| 指标 | 值 |", "|------|-----|"]
    for k, v in metrics.items():
        md.append(f"| {esc(k)} | {esc(str(v))} |")
    md += ["", "## 偏误选中片段（应走纠错链）", "",
           "| ID | 片段 | 黄金span | 分流 | span精确 | span包含 | 识别fragment | 讲解摘要 |",
           "|----|------|---------|:---:|:---:|:---:|------------|----------|"]
    for r in bias_rows:
        if r.get("error"):
            md.append(f"| {r['id']} | {esc(r['text'])} | — | ERR:{esc(r['error'])}")
        else:
            md.append(f"| {r['id']} | {esc(r['text'])} | {esc(r['golden_span'])} | {'✓' if r['split_ok'] else '✗'} | {'✓' if r['span_exact'] else ''} | {'✓' if r['span_cover'] else ''} | {esc(r['fragment'])[:20]} | {esc(r['expl_full'].split('|||')[0])[:50]}")
    md += ["", "## 正确选中片段（应走泛讲解·零误报）", "",
           "| ID | 片段 | 误报 | 合规 | 要点数(≥3) | 讲解摘要 |", "|----|------|:---:|:---:|:---:|----------|"]
    for r in correct_rows:
        if r.get("error"):
            md.append(f"| {r['id']} | {esc(r['text'])} | ERR:{esc(r['error'])}")
        else:
            kp_ok = r.get("kp_ok", r.get("kp_n", 0) >= 3)
            md.append(f"| {r['id']} | {esc(r['text'])} | {'✗' if not r['no_false_pos'] else ''} | {'✓' if r['plain_ok'] else '✗'} | {r.get('kp_n', 0)}{'' if kp_ok else '⚠'} | {esc(r['expl_full'])[:60]}")
    md += ["", "## 完整讲解（人工评审版·不定长，勿截断）", ""]
    for grp in (bias_rows, correct_rows):
        for r in grp:
            if r.get("error"):
                continue
            md += ["### " + str(r["id"]), esc(r["text"]), "",
                   "> " + (r.get("expl_full") or "（无讲解）").replace("\n", "\n> "), ""]
    md += ["", "## 指标口径与金标标注口径", "",
           "**自动指标口径**：",
           "- 偏误分流正确率：应走纠错链的偏误句被识别到（split_ok）的比例；",
           "- span 精确命中率：识别 fragment 与黄金 span **逐字一致**的比例（口径偏严，低估定位能力）；",
           "- span 包含率：识别 fragment **覆盖**黄金 span（允许边界偏宽）的比例，为主定位口径；",
           "- 正确句误报率：正确片段被误判为偏误的比例（ham，目标 0%）；",
           "- 泛讲解结构合规率：正确句产出（explanation + 非空 key_points）的比例；",
           "- 泛讲解要点数下限(≥3)达标率：key_points ≥3 的比例（P2 定标：产品标准为每个语言点一个独立要点、不少于 3 条）。",
           "",
           "**金标标注口径（边界句说明）**：",
           "- 边界句以语境为前提：ERR-003「你喝水吧？」（是非问该用「吗」）、ERR-031「你几岁？」（成人问年龄宜「多大」）等语用类偏误依赖上下文；裸句无上下文时识别引擎按「宁漏勿错」不报，属预期保守行为，计入漏报但标注为上下文相关；",
           "- golden 内 status / arbitrator 字段：disputed 为二语仲裁残留争议句，不据此判错对，仅在报告中单列；",
           "- 人工评审以「完整讲解」段全文为准，表格摘要仅供定位跳转。"]
    open(path, "w", encoding="utf-8").write("\n".join(md))


if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
# scripts/style_check.py
# P0.16 · tutor 措辞抽检：扫描历史 assistant 回复，自动标注"AI 味"风险点，
# 生成 reports/style_check.md（含人填自然度 1-5 的评分表），供定期人工审核回归。
# 用法：
#   python scripts/style_check.py                      # 默认扫 data/memory_*.json
#   python scripts/style_check.py --src data/memory_x.json
#   python scripts/style_check.py --text "直接给这段文字判"
#   python scripts/style_check.py --limit 20 --min-chars 12   # 抽样上限/最短样本
# 输出：reports/style_check.md（自动标注风险标记 + 自然度空白评分列）
import argparse
import glob
import json
import os
import re
import sys

_PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT not in sys.path:
    sys.path.insert(0, _PROJECT)

# AI 味风险标记（来自 skills/tutor-style/SKILL.md §2 禁忌清单）
_MARKERS = [
    ("套话·首先其次", re.compile(r"(首先[^。；\n]{0,10}[其次][^。；\n]{0,10}[最后])"
                                 r"|(其次[^。；\n]{0,10}[最后])")),
    ("套话·首先", re.compile(r"首先[,，]?\s*(其次|然后|接着)")),
    ("收尾套话·总之", re.compile(r"(总之|总而言之|综上所述|总结一下)")),
    ("空洞鼓励·你真棒", re.compile(r"(你真棒|你真厉害|很好不错|太棒了|好棒)")),
    ("空泛鼓励·加油", re.compile(r"(加油[！!。]?|继续努力[！!。]?|再接再厉)")),
    ("空话安抚·多…就会", re.compile(r"多[听说读写练][^。；\n]{0,8}就(会|好)")),
    ("排比·是不是连续", re.compile(r"(是不是[^。？\n]{0,14}？?[是不是])")),
    ("排比·反问堆叠", re.compile(r"(是不是[^。？\n]{0,20}[？?]\s*(是不是|难道))")),
    ("术语堆砌", re.compile(r"(能愿动词|动态助词|结果补语|程度补语|主谓宾|宾语前置|"
                            r"复合趋向补语|把字句|被字句|量词短语)")),
]

# 比"段落"更严：单条以句号结尾却仍未换行且超长的句子（<180 判纸面论文感）
_LONG_BLOCK = 180


def _extract_assistant_texts(data_dir):
    """从 data/memory_*.json 抽取 assistant 回复样本（去重，截长）。"""
    samples = []
    seen = set()
    for fp in sorted(glob.glob(os.path.join(data_dir, "memory_*.json"))):
        try:
            with open(fp, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:  # noqa: BLE001 跳过损坏文件
            continue
        sessions = d.get("sessions") or {}
        for conv in sessions.values():
            for m in (conv.get("messages") if isinstance(conv, dict) else []) or []:
                if isinstance(m, dict) and m.get("role") == "assistant":
                    txt = str(m.get("content") or "").strip()
                    if not txt:
                        continue
                    key = txt[:60]
                    if key in seen:
                        continue
                    seen.add(key)
                    samples.append({"id": f"{os.path.basename(fp)}:{m.get('id','')}",
                                    "text": txt})
    return samples


def _mark(text):
    """返回命中的 AI 味风险标记列表（去重，保持顺序）。"""
    hits = []
    for name, rx in _MARKERS:
        if rx.search(text) and name not in hits:
            hits.append(name)
    if len(text) > _LONG_BLOCK:
        hits.append(f"长段落(>{_LONG_BLOCK}字)")
    return hits


def _score_md(samples, limit, min_chars):
    picked = [s for s in samples if len(s["text"]) >= min_chars][:limit]
    lines = ["# Tutor 措辞抽检（自然度人工评分）",
             "",
             f"> 脚本: scripts/style_check.py · 样本: {len(picked)}/{len(samples)} 条"
             f"（{_PROJECT}\\reports\\style_check.md）",
             "> 自动标注只提示『可能有的 AI 味』，以人工自然度 1-5 为准（5=很自然）。",
             "",
             "| # | 来源 | 可能AI味 | 片段 | 自然度(1-5) |",
             "|---|------|---------|------|:---:|", ]
    for i, s in enumerate(picked, 1):
        hits = _mark(s["text"]) or ["—"]
        frag = s["text"].replace("\n", "␤")[:70]
        lines.append(f"| {i} | {s['id']} | {', '.join(hits)} | {frag} | |")
    if not picked:
        lines.append("（无满足长度条件的样本，请先用真实对话产生 assistant 回复。）")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="tutor 措辞抽检")
    ap.add_argument("--src", help="memory json/目录（默认 data/）")
    ap.add_argument("--text", help="直接判一段文字")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--min-chars", type=int, default=12)
    ap.add_argument("--out", default=os.path.join(_PROJECT, "reports",
                                                  "style_check.md"))
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    if args.text:
        samples = [{"id": "cli", "text": args.text}]
    else:
        src = args.src or os.path.join(_PROJECT, "data")
        samples = _extract_assistant_texts(src if os.path.isdir(src) else os.path.dirname(src))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(_score_md(samples, args.limit, args.min_chars))
    hit_n = sum(1 for s in samples if _mark(s["text"]))
    print(f"样本 {len(samples)} 条，{hit_n} 条含疑似AI味标记；报告 → {args.out}")


if __name__ == "__main__":
    main()
# coding=utf-8
# P0.13: 识别 grammar 空且被 LLM 填样板句的点 → 打 auto_pending 标 + 生成审查清单
import json
import os
import re
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX_JSON = os.path.join(ROOT, "datasets", "grammar_examples.json")
SYL_JSON = os.path.join(ROOT, "datasets", "syllabus_hsk30_2025.json")
REVIEW_JSON = os.path.join(ROOT, "reports", "_p13_review_list.json")

data = json.load(open(EX_JSON, encoding="utf-8"))
syl = json.load(open(SYL_JSON, encoding="utf-8"))
syl_map = {p["id"]: p for p in syl["points"]}
ex = data["examples"]

# 重建 LLM 参与点集合：缺失清单 67 + refill 10
miss = json.load(open(os.path.join(ROOT, "reports", "_p13_missing_list.json"), encoding="utf-8"))
REFILL_IDS = ["hsk30-g2-010", "hsk30-g2-011", "hsk30-g2-037", "hsk30-g2-039",
              "hsk30-g2-040", "hsk30-g2-072", "hsk30-g3-009", "hsk30-g3-010",
              "hsk30-g4-003", "hsk30-g4-061"]
llm_points = {m["id"] for m in miss} | set(REFILL_IDS)

# 1. 找 grammar 为空/无结构的点（这些的例句是 LLM 兜底样板 → auto_pending）
pending = []
for sid in ex:
    p = syl_map.get(sid)
    if not p:
        continue
    grammar = (p.get("grammar") or "").strip()
    name = (p.get("name") or "").strip()
    # grammar 空 → 无结构锚点，样例是兜底样板
    if not grammar:
        pending.append(sid)

print(f"g shown by LLM template as empty-structure (auto_pending): {len(pending)}")
for sid in pending:
    print("  ", sid, syl_map[sid].get("name"))

# 2. 打标：在 data['metadata'] 记录待审 id 列表
data.setdefault("metadata", {})["auto_pending"] = sorted(pending)
data["metadata"]["coverage"]["l1-4_total"] = 339
data["metadata"]["coverage"]["l1-4_covered"] = sum(1 for s in ex if re.match(r"hsk30-g[1-4]-", s))
data["metadata"]["coverage"]["l1-4_reliable"] = sum(
    1 for s in ex if re.match(r"hsk30-g[1-4]-", s) and s not in set(pending))
data["metadata"]["coverage"]["total_points"] = len(ex)
json.dump(data, open(EX_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# 3. 生成审查清单（含来源标注：txt自带 vs LLM补）
review = []
for sid, examples in sorted(ex.items()):
    p = syl_map.get(sid, {})
    review.append({
        "id": sid,
        "level": p.get("level"),
        "category": p.get("category", ""),
        "name": p.get("name", ""),
        "grammar": p.get("grammar", ""),
        "origin": "llm" if sid in llm_points else "txt",
        "pending": sid in set(pending),
        "example_count": len(examples),
        "examples": examples[:4],
    })
json.dump(review, open(REVIEW_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"\n审查清单 {len(review)} 条 → {REVIEW_JSON}")
print("覆盖: l1-4", data["metadata"]["coverage"]["l1-4_covered"])
# coding=utf-8
# 生成缺失例句待补清单 (level1-4) → reports/_p13_missing_list.json
import json
import os
import re
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = json.load(open(os.path.join(ROOT, "datasets", "grammar_examples.json"), encoding="utf-8"))["examples"]
SYL = json.load(open(os.path.join(ROOT, "datasets", "syllabus_hsk30_2025.json"), encoding="utf-8"))

miss = []
for p in SYL["points"]:
    m = re.match(r"hsk30-g(\d)-(\d+)", p["id"])
    if not m:
        continue
    lvl = int(m.group(1))
    if lvl > 4:
        continue
    if p["id"] not in EX:
        miss.append({
            "id": p["id"],
            "level": lvl,
            "category": p.get("category", ""),
            "name": p.get("name", ""),
            "desc": p.get("desc", ""),
            "grammar": p.get("grammar", ""),
            "example_targets": (p.get("desc") or "")[:120],
        })

miss.sort(key=lambda x: (x["level"], x["id"]))
out = os.path.join(ROOT, "reports", "_p13_missing_list.json")
json.dump(miss, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
from collections import Counter
print("缺失点总数:", len(miss))
print("按级:", dict(sorted(Counter(x['level'] for x in miss).items())))
print("样例:", json.dumps(miss[0], ensure_ascii=False))
print("wrote:", out)
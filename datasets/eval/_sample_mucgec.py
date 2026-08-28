# -*- coding: utf-8 -*-
"""方向二采样预检：从 retell_golden.json(MuCGEC) 筛候选句，供人工挑 → 转评测样本"""
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(BASE, "retell_golden.json"), encoding="utf-8"))
items = d["items"]
print("总计:", len(items))

cands = []
for it in items:
    s = it["raw_sentence"]
    refs = it["references"]
    # 候选偏好：句长短(单点)、参考>=2、原句含明显可修正点
    if 6 <= len(s) <= 40 and len(refs) >= 2:
        cands.append(it)

print("短句候选(6-40字, ref>=2):", len(cands))

# 粗按句长分组展示候选
buckets = {"短(<=15)": [], "中(16-25)": [], "稍长(26-40)": []}
for it in cands:
    n = len(it["raw_sentence"])
    k = "短(<=15)" if n <= 15 else ("中(16-25)" if n <= 25 else "稍长(26-40)")
    if len(buckets[k]) < 10:
        buckets[k].append(it)

for k, arr in buckets.items():
    print(f"\n===== {k} =====")
    for it in arr:
        r0 = it["references"][0]
        print(f"[{it['source_id']}] {it['raw_sentence']}  →  {r0}")
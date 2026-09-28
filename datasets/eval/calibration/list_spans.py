# -*- coding: utf-8 -*-
import csv, json, os, sys
sys.stdout.reconfigure(encoding="utf-8")
_HERE = os.path.dirname(os.path.abspath(__file__))
human = {r["id"].strip(): int(r["你的focus分(1-5)"]) for r in csv.DictReader(open(os.path.join(_HERE, "annotation_user.csv"), encoding="utf-8-sig")) if r["你的focus分(1-5)"].strip()}
judge = json.load(open(os.path.join(_HERE, "judge_verdicts_focus.json"), encoding="utf-8"))
dmap = {1:"偏误定位",2:"讲解四段",3:"复述验证",4:"介入时机",5:"语言分层",6:"归因谨慎",7:"任务真实",8:"角色代入",9:"非命令性",10:"对话推进"}
cases = {c["id"]: (c["dim"], c["quality"], c["text"]) for c in json.load(open(os.path.join(_HERE, "calibration_cases.json"), encoding="utf-8"))["cases"]}
rows = []
for cid in sorted(human):
    if cid in judge and judge[cid]["focus_score"] is not None and abs(human[cid] - judge[cid]["focus_score"]) >= 2:
        dim, q, txt = cases[cid]
        rows.append((cid, dmap[dim], q, human[cid], judge[cid]["focus_score"], txt))
for cid, d, q, h, j, txt in rows:
    print(f"{cid} {d:<6}{q:>5} h={h} j={j} | {txt}")
print(f"\n共 {len(rows)} 条仍差≥2")
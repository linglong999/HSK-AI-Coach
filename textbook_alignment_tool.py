# ============================================================
# 教材对齐工具（P0.14）
#   export <out_csv>  : 从考纲导出"考纲点→教材课次"待填表（全量，按等级分组）
#   ingest <csv>      : 把用户填好课次的 CSV 回写进 datasets/textbook_map.json 的 units
# CSV 列：hsk_id, category, name, level, volume(建议册), lesson(课次·待填), note
# 只回写 level<=4 且填了 lesson 的行；其余保留兜底（不入 units）。
# ingest 语义：volume/lesson 同时为空 = 兜底（不入 units）。note 三态保留（已核·注释直接命中/已核·邻近归属/已核·教材未覆盖）。
# ============================================================

import csv
import json
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
SYL = os.path.join(_ROOT, "datasets", "syllabus_hsk30_2025.json")
MAP = os.path.join(_ROOT, "datasets", "textbook_map.json")

COLUMNS = ["hsk_id", "category", "name", "level", "volume", "lesson", "note"]


def _suggest_volume(level) -> str:
    m = {"1": "标准教程1", "2": "标准教程2", "3": "标准教程3",
         "4": "标准教程4上/4下"}
    return m.get(str(level), "（超出教材范围1-4）")


def export(out_csv):
    syl = json.load(open(SYL, encoding="utf-8"))
    pts = syl["points"]
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        # 按 level 分组输出，便于按册核对
        for pt in sorted(pts, key=lambda p: (str(p.get("level", "")), p.get("id", ""))):
            lv = str(pt.get("level", ""))
            w.writerow([
                pt.get("id", ""), pt.get("category", ""), pt.get("name", ""),
                lv, _suggest_volume(lv), "", pt.get("desc", "")[:40],
            ])
    print("已导出:", out_csv, "（共", len(pts), "行）")


def ingest(csv_path):
    # 按（volume, lesson）聚合：同册同课多个考纲点并成一个 unit（kp_ids 并列）
    groups = {}
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            hsk = (row.get("hsk_id") or "").strip()
            lv = (row.get("level") or "").strip()
            lesson_raw = (row.get("lesson") or "").strip()
            vol = (row.get("volume") or "").strip()
            note = (row.get("note") or "").strip()
            if not hsk:
                continue
            if str(lv) in ("5", "6", "7-9", "7", "8", "9"):
                continue  # 超教材范围1-4，保留兜底
            if not vol or not lesson_raw:
                continue  # volume/lesson 任一为空即兜底，不入表
            digits = "".join(ch for ch in lesson_raw if ch.isdigit())
            lesson = int(digits) if digits else lesson_raw
            key = (vol, lesson)
            g = groups.setdefault(key, {
                "volume": vol, "lesson": lesson,
                "title": (row.get("name") or "").strip()[:48],
                "kp_ids": [], "notes": [],
            })
            g["kp_ids"].append(hsk)
            if note:
                g["notes"].append(note)

    units = []
    for g in groups.values():
        # note 三态去重合并且保留顺序
        notes = list(dict.fromkeys(g["notes"]))
        units.append({
            "volume": g["volume"],
            "lesson": g["lesson"],
            "title": g["title"],
            "kp_ids": g["kp_ids"],
            "note": "；".join(notes) if notes else "人工审核转正（P0.14）",
        })

    data = json.load(open(MAP, encoding="utf-8"))
    data["books"]["stdcourse_hsk"]["units"] = units
    data["status"] = "verified" if units else "draft_mapping_pending"
    with open(MAP, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("已回写 textbook_map.json，units =", len(units))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "export":
        export(sys.argv[2] if len(sys.argv) > 2 else
               os.path.join(_ROOT, "reports", "考纲点-教材课次-待填.csv"))
    elif cmd == "ingest":
        ingest(sys.argv[2])
    else:
        print("用法: python textbook_alignment_tool.py export [out.csv] | ingest <filled.csv>")
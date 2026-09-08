# -*- coding: utf-8 -*-
# ============================================================
# scripts/build_syllabus_diff.py —— P0.15 与 v1_4 知识点对照
#
# 把 593 条考纲与现有 25 个 knowledge_points_v1_4 逐一对齐，
# 产出 reports/syllabus_diff.md（留档，不覆盖、不改数据）。
#
# 对照键（A2 拍板：多键归一化，不用"语法内容"作为唯一键）：
#   1 主键  item（细目，47→实测139条为空时退化）
#   2 次键  name（类别名称）
#   3 末键  grammar（语法内容，句式公式，仅作辅助）
#   别名表：被动句↔被字句 等（显式映射，见 ALIAS）
#
# 三级输出：
#   [A] 精确命中（任一归一键完全匹配一个 kp）
#   [B] 疑似命中（模糊，供人工复核）
#   [C] 未命中 / 疑似，需人工裁定
# ============================================================

import json
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.syllabus import SyllabusDatabase

DATASETS = os.path.join(_PROJECT_ROOT, "datasets")
REPORTS = os.path.join(_PROJECT_ROOT, "reports")
SYLLABUS = os.path.join(DATASETS, "syllabus_hsk30_2025.json")
KP = os.path.join(DATASETS, "knowledge_points_v1_4.json")

# 显式别名归一：考纲文本 → 统一术语。被动句↔被字句 为一例。
ALIAS = {
    "被字句": "被动句",
    "把字句": "把字句",
    "被动句": "被动句",
    "存现句": "存现句",
    "比较句": "比较句",
}


def norm_key(s):
    """归一化检索键：去空白/括号/序号。"""
    if not s:
        return ""
    s = re.sub(r"[\s（）()\[\]【】°]", "", str(s))
    s = re.sub(r"^\d+", "", s)      # 去前导序号（如"1会"）
    return s


def alias(s):
    return ALIAS.get(norm_key(s), norm_key(s))


def load_kps():
    d = json.load(open(KP, encoding="utf-8"))
    return d["knowledge_points"]         # {id: kp}


def main():
    os.makedirs(REPORTS, exist_ok=True)
    db = SyllabusDatabase.load(SYLLABUS)
    kps = load_kps()

    # v1_4 的 kp 用 knowledge_point（名称）做唯一键；考纲侧三级归一后碰它
    kp_names = {}          # 归一名称 → kp id（别名归一）
    kp_raw = {}            # 原名 → kp id（用于报告展示原名）
    for kid, kp in kps.items():
        nm = kp.get("knowledge_point", "") or kp.get("id", "")
        kp_names.setdefault(alias(nm), kid)
        kp_raw[kid] = nm

    matched = []   # (syllabus point, kind, kp_id)
    unmatched = []

    for p in db.all():
        # 三级归一键：细目>类别名称>语法内容，全部 alias 归一后碰 kp 名称
        keys = [alias(p["item"]), alias(p["name"]), alias(p["grammar"])]
        hit = None
        for k in keys:
            if k and k in kp_names:
                hit = kp_names[k]
                break
        if hit:
            matched.append((p, next((kn for kn in keys if kn and kn in kp_names), ""), hit))
        else:
            unmatched.append(p)

    # 映射命中的键类型，用于分级展示
    def key_kind(p):
        for k, name in (("item", norm_key(p["item"])),
                        ("name", norm_key(p["name"])),
                        ("grammar", norm_key(p["grammar"]))):
            if name and alias(name) in kp_names:
                return k, alias(name)
        return ("?", "")

    exact = [m for m in matched if key_kind(m[0])[0] == "item"]
    gram_match = [m for m in matched if key_kind(m[0])[0] == "grammar"]
    name_match = [m for m in matched if key_kind(m[0])[0] == "name"]

    lines = []
    lines.append("# 考纲 vs v1_4 知识点对照报告")
    lines.append("")
    lines.append("- 考纲 syllabus_hsk30_2025(%d 条) vs v1_4 knowledge_points(%d 条)" %
                 (len(db.all()), len(kps)))
    lines.append("- 命中 %d · 未命中 %d（其中细目=item 命中 %d / 语法=grammar 命中 %d / "
                 "类别=name 疑似 %d）" %
                 (len(matched), len(unmatched), len(exact), len(gram_match), len(name_match)))
    lines.append("")
    lines.append("## A. 精确命中（细目 item 归一）")
    lines.append("")
    for p, kind, kid in exact:
        lines.append("- `%s` [%s] → kp `%s`" % (p["id"], p["grammar"][:30], kid))
    lines.append("")
    lines.append("## B. 语法公式命中（grammar 归一，偏弱）")
    lines.append("")
    for p, kind, kid in gram_match:
        lines.append("- `%s` [%s] → kp `%s`" % (p["id"], p["grammar"][:30], kid))
    lines.append("")
    lines.append("## B2. 类别名称疑似（name 归一，供人工裁定）")
    lines.append("")
    for p, kind, kid in name_match:
        lines.append("- `%s` [%s] ~ kp `%s`（类别名称）" % (p["id"], p["grammar"][:30], kid))
    lines.append("")
    lines.append("## C. 未命中（可能的新知识点 / 需人工补）")
    lines.append("")
    for p in unmatched:
        lines.append("- `%s` [%s]（%s）" % (p["id"], p["grammar"][:40], p["name"]))

    out = os.path.join(REPORTS, "syllabus_diff.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("报告已生成:", out)
    print("item命中=%d grammar命中=%d name疑似=%d 未命中=%d" %
          (len(exact), len(gram_match), len(name_match), len(unmatched)))


if __name__ == "__main__":
    main()
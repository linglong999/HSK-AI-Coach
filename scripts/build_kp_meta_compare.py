# -*- coding: utf-8 -*-
"""P0.15 转正辅助 · 精简对照表生成

把 25 个 v1_4 知识点收敛到"最贴切的考纲句式条目"（按 item 句式名匹配，
而非 grammar 公式——避免单字命中泛化）。输出 reports/kp_meta_compare.md，
供 A 角色逐条裁定：在 "裁定" 列填 采纳/驳回/仅部分（不覆盖源数据）。
"""
import csv
import json
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

DATASETS = os.path.join(_PROJECT_ROOT, "datasets")
REPORTS = os.path.join(_PROJECT_ROOT, "reports")

# 检索归一化：去 引号/书名号/括号/空白/破折号，避免 “把”字句1 阻断子串
_PUNCT = re.compile(r"[\s“”‘’\"'《》()（）…—]")

# v1_4 知识点 → 考纲 item 句式名关键词（收敛用）
KP_ITEM_KEYS = {
    "kp-ba-sentence": ["把”字句"],
    "kp-bei-sentence": ["被”字句", "被动句"],
    "kp-liangci": ["量词"],
    "kp-nengyuan-dongci": ["能愿动词"],
    "kp-bi-sentence": ["比较句"],
    "kp-he-yiyang": ["一样"],
    "kp-le-dynamic": ["动态助词“了”", "动作的态·完成态"],
    "kp-zhe": ["动态助词“着”", "动作的态·进行态", "动作的态·持续态"],
    "kp-guo": ["动态助词“过”", "动作的态·经历态"],
    "kp-jiuguo-jiegou": ["结果补语", "动补短语", "程度补语"],
    "kp-quxiang-buyu": ["趋向补语"],
    "kp-de-di-de": ["结构助词"],
    "kp-cunxian-ju": ["存现句"],
    "kp-liandong-ju": ["连动句"],
    "kp-standing-shi": ["兼语句"],
    "kp-shide-sentence": ["“是…的”", "“是……的”", "强调句"],
    "kp-dongci-shuangbin": ["双宾语句", "双宾语"],
    "kp-zhuangyu-chezhi": ["状语"],          # 状语作句法成分（语序见语法内容）
    "kp-zhongci-zhitou": ["代词"],            # 词类·代词（人称/指示/疑问）
    "kp-preposition-zaizai": ["介词"],
    "kp-jietiaoyu-tiaojian": ["复句"],
    "kp-haishi-xuanze": ["疑问句", "选择问", "正反问"],
    "kp-chengdu-jieci": ["程度副词"],
    "kp-zhizhi-dao": ["时量补语", "动量补语"],
    "kp-zhongci-fugao": ["范围副词"],
}

# 句式名（item）命中为空时的回退：动态助词/介词/补语/状语等在考纲里
# 分属「动作的态」类别口令、name=介词、数量补语、grammar 状语等，非 item 句式名。
# 值 = list[(field, keyword)]，任一命中即入候选；空档则由 A 角色裁定映射。
KP_FALLBACK = {
    "kp-le-dynamic": [("item", "完成态"), ("item", "变化态")],
    "kp-zhe": [("item", "持续态")],
    "kp-guo": [("item", "经历态")],
    "kp-preposition-zaizai": [("name", "介词")],
    "kp-zhizhi-dao": [("item", "数量补语")],
    "kp-zhuangyu-chezhi": [("grammar", "状语")],
    "kp-he-yiyang": [("grammar", "一样")],
}

# kp 名 → 已知语义（用于表头）
KP_NAMES = {
    "kp-ba-sentence": "“把”字句",
    "kp-bei-sentence": "“被”字句",
    "kp-liangci": "量词",
    "kp-nengyuan-dongci": "能愿动词",
    "kp-bi-sentence": "比较句",
    "kp-he-yiyang": "“和…一样”比较",
    "kp-le-dynamic": "动态助词“了”",
    "kp-zhe": "动态助词“着”",
    "kp-guo": "动态助词“过”",
    "kp-jiuguo-jiegou": "结果补语",
    "kp-quxiang-buyu": "趋向补语",
    "kp-de-di-de": "结构助词的地得",
    "kp-cunxian-ju": "存现句",
    "kp-liandong-ju": "连动句",
    "kp-standing-shi": "兼语句",
    "kp-shide-sentence": "“是…的”强调句",
    "kp-dongci-shuangbin": "双宾语句",
    "kp-zhuangyu-chezhi": "状语语序",
    "kp-zhongci-zhitou": "代词使用",
    "kp-preposition-zaizai": "介词及其误用",
    "kp-jietiaoyu-tiaojian": "关联词/条件复句",
    "kp-haishi-xuanze": "选择问/正反问",
    "kp-chengdu-jieci": "程度副词",
    "kp-zhizhi-dao": "时量补语",
    "kp-zhongci-fugao": "范围副词",
}


def main():
    sy = json.load(open(os.path.join(DATASETS, "syllabus_hsk30_2025.json"), encoding="utf-8"))
    points = sy["points"]

    lines = []
    lines.append("# v1_4 知识点 → 考纲句式条目 · 精简待裁定对照表")
    lines.append("")
    lines.append("> **判定维度（口径声明）**：句式类（把/被/比较/补语/…）按考纲 `item` 句式名匹配；"
                 "词类（量词/代词/副词/介词/能愿动词）天然无 item 名，按 `name`+item 子类匹配。"
                 "回退行已在表格内标注 **【非句式名命中·词类/功能回退】**，勿与句式命中同权解读。")
    lines.append("> 每条给「推荐采纳」的候选句式条目，含级别与语法内容，供 A 角色逐条裁定。")
    lines.append("> 裁定列：**采纳** = 该 kp 对应该句式；**驳回** = 对应错/应换；**仅部分** = 只采纳部分级别条目。")
    lines.append("> 同时标注：该知识点在考纲中的**跨级别分布**（是否多级、有无缺失级别）。")
    lines.append("> 此表只留前 9 条摘要；全量候选见附表 `kp_meta_compare_full.csv`（kp_id × syllabus_id × level），凑数以附表为准。")
    lines.append("> 空白单元格为待裁定占位。A 角色在「裁定」列填 采纳/驳回/仅部分，可加备注。")
    lines.append("")
    lines.append("| 知识点 | 考纲句式条目(id / item / 级别) | 语法内容摘要 | 跨级分布 | 裁定 |")
    lines.append("|---|---|---|---|---|")

    # 考纲按 item 归组：item → list[(id, level, grammar)]
    by_item = {}
    for p in points:
        item = p["item"].strip()
        if not item:
            continue
        by_item.setdefault(item, []).append(p)

    csv_rows = []  # 全量候选，供附表导出
    for kid, keys in KP_ITEM_KEYS.items():
        # 收集命中考纲条目（item 包含任一关键词）
        cand = []
        for item, plist in by_item.items():
            if any(k in item for k in keys):
                for p in plist:
                    cand.append(p)
        fallback = False
        if not cand:
            # 句式名空档：回退到 类别口令/name/grammar 的确定映射
            for field, kw in KP_FALLBACK.get(kid, []):
                for p in points:
                    text = _PUNCT.sub("", str(p.get(field, "")))
                    if kw in text:
                        cand.append(p)
                fallback = True
        if not cand:
            lines.append("| %s（%s） | **无句式名命中** | — | 需人工补/判超纲 |  |"
                         % (kid, KP_NAMES.get(kid, kid)))
            continue
        # 去重 id，按级别排序
        seen = {}
        for p in cand:
            seen.setdefault(p["id"], p)
        ord = sorted(seen.values(), key=lambda p: str(p["level"]))
        for p in ord:
            csv_rows.append((kid, KP_NAMES.get(kid, kid), "fallback" if fallback else "item",
                             p["id"], p["level"], p["item"],
                             (p.get("grammar") or "").replace('"', '""')))
        # 跨级分布摘要
        lv_set = sorted({str(p["level"]) for p in ord})
        dist = "、".join(lv_set)
        dist_note = "多级（%d级）" % len(lv_set) if len(lv_set) > 1 else "单级"
        cell = "<br>".join(
            "`%s` `%s` · L%s · %s" % (p["id"], p["item"], p["level"],
                                      (p["grammar"] or "")[:28])
            for p in ord[:9])
        if fallback:
            cell = "**【非句式名命中·词类/功能回退】**<br>" + cell
        if len(ord) > 9:
            cell += "<br>…共 %d 条" % len(ord)
        lines.append("| %s（%s） | %s | %s |  |"
                     % (kid, KP_NAMES.get(kid, kid), cell, "%s (%s)，共 %d 条" % (dist, dist_note, len(ord))))

    out = os.path.join(REPORTS, "kp_meta_compare.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    out_csv = os.path.join(REPORTS, "kp_meta_compare_full.csv")
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kp_id", "kp_name", "match_type", "syllabus_id", "level", "item", "grammar"])
        w.writerows(csv_rows)
    print("精简对照表已生成:", out)
    print("全量候选附表已生成:", out_csv, "共", len(csv_rows), "条")
    print("覆盖知识点:", sum(1 for k in KP_ITEM_KEYS) - 0)


if __name__ == "__main__":
    main()
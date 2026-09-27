# -*- coding: utf-8 -*-
"""cal_b · 生成用户打分表（逐条 focus 维 1-5 + judge 参考）

输入：calibration_cases.json（构建锚）+ judge_verdicts.json（机器 focus 分）
输出：annotation_user.csv —— 用户对每个样本只打「focus_dim」一维 1-5，
      judge 该维分数预填作参考（独立评，不照抄）。
"""
import csv
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_CASES = os.path.join(_HERE, "calibration_cases.json")
_VERD = os.path.join(_HERE, "judge_verdicts.json")
_OUT = os.path.join(_HERE, "annotation_user.csv")

DIM_NAME = {
    1: "偏误定位准确", 2: "讲解四段完整", 3: "复述验证严格", 4: "介入时机合理",
    5: "语言分层守约", 6: "归因谨慎", 7: "任务真实性", 8: "角色代入",
    9: "非命令性", 10: "对话推进感",
}
QUALITY_ANCHOR = {"high": "高分样例（应≈4-5）", "mid": "中分样例（应≈3）",
                  "low": "低分样例（应≈1-2）"}


def main():
    cases = json.load(open(_CASES, encoding="utf-8"))["cases"]
    verdicts = json.load(open(_VERD, encoding="utf-8"))
    with open(_OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "focus_dim", "focus维度", "构建质量锚", "judge在该维参考",
                    "文本", "你的focus分(1-5)", "你判该样本是否达标(pass/fail)", "备注"])
        for c in cases:
            vid = verdicts.get(c["id"], {})
            judge_focus = (vid.get("per_dim") or {}).get(str(c["dim"]), "")
            w.writerow([
                c["id"], c["dim"], DIM_NAME.get(c["dim"], "?"),
                QUALITY_ANCHOR.get(c["quality"], c["quality"]),
                judge_focus, c["text"], "", "", "",
            ])
    n = len(cases)
    print(f"wrote {n} rows -> {_OUT}")


if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
"""一次性脚本：把独立评分回填进 annotation_user.csv（保留原列结构/BOM）。
打分口径：rubric.md v2 各维锚；pass ⟺ focus分>=4；存疑项在备注以「存疑：」开头。
"""
import csv, os

HERE = r"D:\HuaweiMoveData\Users\曹玲珑\Desktop\HSK\HSK-AI-Coach\datasets\eval\calibration"
CSV = os.path.join(HERE, "annotation_user.csv")

# id -> (score, note)；reach 由 score>=4 派生
S = {
    # dim7 任务真实性
    "CAL-001": (5, ""),
    "CAL-002": (4, "judge分歧(j=1)：judge理由称「空对话」与文本不符，疑judge输入形态有误"),
    "CAL-003": (4, "judge分歧(j=2)"),
    "CAL-004": (4, ""),
    "CAL-005": (3, "judge分歧(j=1)：有任务但流于形式=rubric 3"),
    "CAL-006": (3, "judge分歧(j=1)"),
    "CAL-007": (3, "judge分歧(j=1)"),
    "CAL-008": (3, "judge分歧(j=1)"),
    "CAL-009": (3, "judge分歧(j=1)"),
    "CAL-010": (1, "另注：「中译中」构造瑕疵，不影响低分判定"),
    "CAL-011": (1, ""), "CAL-012": (1, ""), "CAL-013": (1, ""),
    # dim8 角色代入
    "CAL-014": (5, ""),
    "CAL-015": (5, "judge分歧(j=3)"),
    "CAL-016": (5, ""),
    "CAL-017": (5, "judge分歧(j=1)"),
    "CAL-018": (3, "judge分歧(j=1)"),
    "CAL-019": (3, "存疑：仅发出角色指令未实际代入，2/3 之间，取3"),
    "CAL-020": (3, "judge分歧(j=1)"),
    "CAL-021": (3, "judge分歧(j=1)"),
    "CAL-022": (3, "judge分歧(j=1)"),
    "CAL-023": (1, ""), "CAL-024": (1, ""), "CAL-025": (1, ""), "CAL-026": (1, ""),
    # dim9 非命令性
    "CAL-027": (5, ""), "CAL-028": (5, ""), "CAL-029": (5, ""), "CAL-030": (5, ""),
    "CAL-031": (3, "judge分歧(j=1)：半引导半指令"),
    "CAL-032": (3, "存疑：「一定要完整说」偏指令施压，2/3 之间，取3"),
    "CAL-033": (3, "judge分歧(j=1)"),
    "CAL-034": (3, "judge分歧(j=1)"),
    "CAL-035": (3, "存疑：柔化足(慢慢来/试试)但未给选择，3/4 之间，取3；j=2"),
    "CAL-036": (1, ""), "CAL-037": (1, ""), "CAL-038": (1, ""), "CAL-039": (1, ""),
    # dim10 对话推进感
    "CAL-040": (5, ""),
    "CAL-041": (5, "judge分歧(j=2)"),
    "CAL-042": (5, "judge分歧(j=1)"),
    "CAL-043": (5, ""),
    "CAL-044": (3, "judge分歧(j=1)：有回应但循环=rubric 3"),
    "CAL-045": (3, "judge分歧(j=1)"),
    "CAL-046": (3, "judge分歧(j=1)"),
    "CAL-047": (3, "judge分歧(j=1)"),
    "CAL-048": (3, "存疑：同义转述有一定推进，3/4 之间，取3"),
    "CAL-049": (3, "judge分歧(j=1)"),
    "CAL-050": (1, ""), "CAL-051": (1, ""), "CAL-052": (1, ""), "CAL-053": (1, ""),
    # dim2 讲解四段完整
    "CAL-054": (5, "judge分歧(j=2)：四段齐全(为何错/正确/对比/验证)"),
    "CAL-055": (5, "judge分歧(j=3)"),
    "CAL-056": (4, "验证段偏弱(「我再说一遍」非学习者产出)，j=2"),
    "CAL-057": (2, "与锚分歧(锚中分)：仅「错+正确」两段，无为何/对比/验证"),
    "CAL-058": (2, "与锚分歧(锚中分)：仅正确+弱验证"),
    "CAL-059": (3, "点名问题类别(了/的)+正确+练习，比057多一段，取3"),
    "CAL-060": (1, ""), "CAL-061": (1, ""), "CAL-062": (1, ""),
    # dim3 复述验证严格
    "CAL-063": (5, ""),
    "CAL-064": (5, "judge分歧(j=2)：拒假懂+复述+判据+降级齐全"),
    "CAL-065": (4, "有复述+降级，缺「两次」判据；j=1分歧"),
    "CAL-066": (3, "judge分歧(j=1)"),
    "CAL-067": (3, "judge分歧(j=1)"),
    "CAL-068": (3, "judge分歧(j=1)"),
    "CAL-069": (2, "存疑：有「拒假懂」但语气威胁且无降级，1/2 之间，取2"),
    "CAL-070": (2, "存疑：有明确判据但苛刻无降级，1/2 之间，取2"),
    "CAL-071": (1, ""),
    # dim4 介入时机合理
    "CAL-072": (5, ""), "CAL-073": (5, ""),
    "CAL-074": (3, "judge分歧(j=1)：打断但针对真错"),
    "CAL-075": (3, "judge分歧(j=1)"),
    "CAL-076": (3, "存疑：语气温和且说明理由，3/4 之间，取3"),
    "CAL-077": (1, ""), "CAL-078": (1, ""),
    # dim1 偏误定位准确
    "CAL-079": (5, ""),
    "CAL-080": (5, "judge分歧(j=3)"),
    "CAL-081": (3, "定位含糊(「有点问题」)，j=2"),
    "CAL-082": (2, "与锚分歧(锚中分)：「改一下」未定位到点"),
    "CAL-083": (3, "judge分歧(j=1)：带 hedge 的定位(可能出在补语)"),
    "CAL-084": (3, "judge分歧(j=1)"),
    "CAL-085": (1, ""), "CAL-086": (1, ""),
    # dim5 语言分层守约
    "CAL-087": (5, "judge分歧(j=1)：英文脚手架+中文载体完整"),
    "CAL-088": (5, "judge分歧(j=1)"),
    "CAL-089": (3, "judge分歧(j=1)：英文脚手架单薄"),
    "CAL-090": (3, "judge分歧(j=1)：「类似英文」未落实"),
    "CAL-091": (3, "存疑：全程中文无英文脚手架，2/3 之间，取3"),
    "CAL-092": (1, ""), "CAL-093": (1, ""),
    # dim6 归因谨慎
    "CAL-094": (5, ""),
    "CAL-095": (5, "judge分歧(j=1)：「可能…之一」措辞谨慎"),
    "CAL-096": (3, "存疑：谨慎但空泛(但也不一定)，3/4 之间，取3"),
    "CAL-097": (3, "judge分歧(j=1)"),
    "CAL-098": (3, "judge分歧(j=1)"),
    "CAL-099": (1, ""), "CAL-100": (1, ""),
}

with open(CSV, encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
    fields = list(rows[0].keys())

n_fill = 0
for r in rows:
    cid = r["id"].strip()
    if cid in S:
        score, note = S[cid]
        r["你的focus分(1-5)"] = str(score)
        r["你判该样本是否达标(pass/fail)"] = "pass" if score >= 4 else "fail"
        r["备注"] = note
        n_fill += 1

with open(CSV, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

unsure = [cid for cid, (s, note) in S.items() if note.startswith("存疑")]
print(f"filled {n_fill}/100 rows")
print(f"存疑 {len(unsure)} 条: {', '.join(unsure)}")

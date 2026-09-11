# coding=utf-8
# 评估 no7z 数据作为例句源：看能否对应项目 syllabus hsk30-gN-XXX 点
import json, re, os

RO = 'd:/HuaweiMoveData/Users/曹玲珑/Desktop/HSK/HSK-AI-Coach'
no7z = json.load(open(f'{RO}/reports/_no7z_grammar_points.json', encoding='utf-8'))
sy = json.load(open(f'{RO}/datasets/syllabus_hsk30_2025.json', encoding='utf-8'))['points']

def strip(s):
    return re.sub(r'[\s（）()【】、，,；;：:]+', '', s or '')

# no7z 的 id 是 1-01 格式；项目 syllabus 是 hsk30-g1-01。先看编号是否直接对得上
print("=== no7z 层级分布 ===")
from collections import Counter
lvl_c = Counter(x['level'] for x in no7z)
print(dict(sorted(lvl_c.items())))
print("no7z 总数:", len(no7z))

# no7z id "1-01" → 项目期望 "hsk30-g1-01x"? 看项目 id 编号范围
sy_ids = {}
for p in sy:
    m = re.match(r'hsk30-g(\d)-(\d+)', p['id'])
    if m:
        sy_ids.setdefault(int(m.group(1)), []).append(p['id'])
for lv in sorted(sy_ids):
    ids = sy_ids[lv]
    nums = sorted(int(re.match(r'hsk30-g\d-(\d+)', i).group(1)) for i in ids)
    print(f"  level{lv}: 项目点 {len(ids)} 个, 编号范围 {nums[0]}~{nums[-1]}")

# 前10条 no7z
print("\n=== no7z 前12条（id | label） ===")
for x in no7z[:12]:
    print(f"  {x['id']} | {x['label'][:30]} | {len(x['examples'])}例")
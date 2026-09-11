# coding=utf-8
# 并排对照 no7z(1-01...) vs 项目(hsk30-g1-01...) level1，看是否逐条内容对应
import json, re, os

RO = 'd:/HuaweiMoveData/Users/曹玲珑/Desktop/HSK/HSK-AI-Coach'
no7z = json.load(open(f'{RO}/reports/_no7z_grammar_points.json', encoding='utf-8'))
sy = json.load(open(f'{RO}/datasets/syllabus_hsk30_2025.json', encoding='utf-8'))['points']

no1 = [x for x in no7z if x['level'] == 1]
proj1 = [(p['id'], p) for p in sy if re.match(r'hsk30-g1-\d+', p['id'])]
proj1.sort(key=lambda x: int(re.match(r'hsk30-g1-(\d+)', x[0]).group(1)))

def strip(s):
    return re.sub(r'[\s（）()【】、，,；;：:/]+', '', s or '')

print("=== level1: no7z编号 | label（前14字） || 项目id | name | grammar ===")
n = max(len(no1), len(proj1))
for i in range(n):
    a = no1[i] if i < len(no1) else {'id': '?', 'label': '—(超)','examples':[]}
    b = proj1[i] if i < len(proj1) else (('?', {'name':'—(超)'}))
    la = strip(a['label'])[:16]
    lb = (strip(b[1].get('name','')) + '/' + strip(b[1].get('grammar','')))[:20]
    flag = '❌' if la[:6] and lb[:6] and la[:6]!=lb[:6] else ('⚠' if not la[:6] or not lb[:6] else '')
    print(f"  {a['id']:<5} {la:<18} || {b[0]:<12} {lb:<22} {flag}")
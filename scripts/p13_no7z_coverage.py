# coding=utf-8
# 内容级对齐：对每个项目考纲点，用 name+grammar 在 no7z 中找内容匹配的点，报告逐点覆盖率
import json, re, os

RO = 'd:/HuaweiMoveData/Users/曹玲珑/Desktop/HSK/HSK-AI-Coach'
no7z = json.load(open(f'{RO}/reports/_no7z_grammar_points.json', encoding='utf-8'))
sy = json.load(open(f'{RO}/datasets/syllabus_hsk30_2025.json', encoding='utf-8'))['points']

def strip(s):
    return re.sub(r'[\s（）()【】、，,；;：:/•·\-—]+', '', s or '')

def norm(s):
    # 去所有标点和空白，仅留汉字/字母
    return re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', s or '')

# no7z 每级可用的候选（label + examples）
instances = [{'id': x['id'], 'level': x['level'], 'label': x['label'],
              'label_full': x.get('label_full', ''), 'examples': x.get('examples', [])}
             for x in no7z]
inst_by_lv = {1: [d for d in instances if d['level'] == 1],
              2: [d for d in instances if d['level'] == 2],
              3: [d for d in instances if d['level'] == 3],
              4: [d for d in instances if d['level'] == 4]}

def has_any_word(label, words):
    return any(w and strip(w) in strip(label) for w in words.split('、'))

# 对项目每个点找 no7z 匹配
def find_match(syl_pt, lv):
    nm = norm(syl_pt.get('name', ''))
    gm = strip(syl_pt.get('grammar', ''))
    gm_words = [w for w in re.split(r'[、，,；;（）()]', gm) if w]
    for cand in inst_by_lv.get(lv, []):
        cl = strip(cand['label_full'] or cand['label'])
        # 匹配1：语法词命中 label（如项目grammar"有（一）点儿" 在 no7z label 里）
        hit = [w[:2] for w in gm_words if w and strip(w)[:2] in cl and len(strip(w)) >= 2]
        if hit:
            return cand, f"grammar词命中:{hit[0]}"
        # 匹配2：name 相同
        if nm and norm(cand['label']) == nm:
            return cand, "name同"
        # 匹配3：项目名/grammar 前4字符 在 no7z label 里
        if nm and norm(cand['label'])[:4] == nm[:4] and len(nm) >= 4:
            return cand, "name前4"
    return None, None

# 对 level1-4 全跑
total, covered, miss = 0, 0, []
for lv in (1, 2, 3, 4):
    lv_syl = [p for p in sy if re.match(rf'hsk30-g{lv}-\d+', p['id'])]
    for p in sorted(lv_syl, key=lambda x: int(re.match(r'hsk30-g\d-(\d+)', x['id']).group(1))):
        # 只统计 grammar 非空的点（有明确结构的可出题）
        total += 1
        cand, via = find_match(p, lv)
        if cand and cand['examples']:
            covered += 1
        else:
            miss.append((p['id'], p.get('name',''), p.get('grammar','')[:20], via))

print(f"level1-4 项目点总数: {total}")
print(f"  有例句可匹配（no7z）: {covered}")
print(f"  无例句/无匹配: {len(miss)}")
print(f"\n无例句点（前50）:")
for sid, name, gm, via in miss[:50]:
    print(f"  {sid} | {name} | {gm or '(grammar空)'}")
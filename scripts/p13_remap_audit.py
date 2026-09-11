# coding=utf-8
# P0.13 修复：内容级 re-map，把 txt 例句归位到正确的考纲点（不依赖编号）
import json, re, os

RO = 'd:/HuaweiMoveData/Users/曹玲珑/Desktop/HSK/HSK-AI-Coach'
sy = json.load(open(f'{RO}/datasets/syllabus_hsk30_2025.json', encoding='utf-8'))['points']
smap = {p['id']: p for p in sy}
KH = {"1": "一", "2": "二", "3": "三", "4": "四"}

def strip(s):
    return re.sub(r'[\s（）()【】、，,；;：:]+', '', s or '')

# 索引：syllabus 每级的点列表（含 id, name, grammar）
lv_points = {1: [], 2: [], 3: [], 4: []}
for p in sy:
    m = re.match(r'hsk30-g(\d)-(\d+)', p['id'])
    if not m:
        continue
    lv = int(m.group(1))
    if lv in lv_points:
        lv_points[lv].append(p)

def norm_name(s):
    # 归一化语法点名：去空白和标点
    return strip(s)

def match_txt_to_syllabus(txtname, lv):
    """把 txt 块名匹配到 syllabus 点。返回 (syllabus_id, 匹配方式) 或 (None, reason)"""
    tn = strip(txtname)
    if not tn:
        return None, 'txt名空'
    # 候选：同级别的点
    cands = lv_points[lv]
    # 方式1：txt 名与 syllabus 的 name 精确匹配（如"方位名词"可能没直接点）
    for p in cands:
        if strip(p.get('name', '')) == tn:
            return p['id'], 'name精确'
    # 方式2：txt 名包含 syllabus name 或反之（如上/下/里 → g1-03 名词）
    for p in cands:
        pn = strip(p.get('name', ''))
        if pn and (pn in tn or tn in pn) and max(len(pn), len(tn)) >= 2:
            return p['id'], f'包含({pn})'
    # 方式3：txt 名与 syllabus grammar 内容匹配（grammar 里词的集合）
    for p in cands:
        gm = strip(p.get('grammar', ''))
        # grammar 含 txt 名关键字符（如 txt"会、能" → g1-04 动词 grammar"会、能"）
        if gm and (gm[:2] in tn or tn[:2] in gm):
            return p['id'], 'grammar关键词'
    return None, '无匹配'

# 解析 txt 每级块 -> 提取例句
def parse_blocks(lv):
    path = f'{RO}/reports/_hj_download/HSK{lv}.txt'
    raw = open(path, encoding='utf-8').read()
    blocks = []
    cur = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(rf'【{KH[lv]}(\d{{1,2}})】(.+)', line)
        if m:
            if cur:
                blocks.append(cur)
            cur = {'name': m.group(2).strip(), 'examples': []}
            continue
        if cur is not None:
            if any(c in line for c in ('。', '？', '！', '.', '?')):
                sents = re.split(r'(?<=[。？!?.])', line)
                for s in sents:
                    s = s.strip()
                    if s:
                        cur['examples'].append(s)
    if cur:
        blocks.append(cur)
    return blocks

# 跑全4级，统计映射
stats = {'name精确': 0, '包含': 0, 'grammar关键词': 0, '无匹配': 0}
unmatched = []
mapping = {}  # txt块 -> syllabus_id
for lv in ('1', '2', '3', '4'):
    for b in parse_blocks(lv):
        lvi = int(lv)
        sid, via = match_txt_to_syllabus(b['name'], lvi)
        if sid:
            mapping[(lvi, b['name'])] = sid
            stats[via] = stats.get(via, 0) + 1
        else:
            stats['无匹配'] += 1
            unmatched.append((lvi, b['name'], len(b['examples'])))

print("=== re-map 匹配统计 ===")
for k, v in stats.items():
    print(f"  {k}: {v}")
print(f"\n=== 无匹配块（txt块 | 例句数） ===")
for lvi, name, n in unmatched[:60]:
    print(f"  L{lvi} | {name[:30]} | {n}例")
print(f"\n无匹配总数: {len(unmatched)}")
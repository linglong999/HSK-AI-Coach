# coding=utf-8
# P0.13 方案①：重建可靠例句库。no7z 内容级匹配 → 只保留真实有例句的点
import json, re, os

RO = 'd:/HuaweiMoveData/Users/曹玲珑/Desktop/HSK/HSK-AI-Coach'
no7z = json.load(open(f'{RO}/reports/_no7z_grammar_points.json', encoding='utf-8'))
sy = json.load(open(f'{RO}/datasets/syllabus_hsk30_2025.json', encoding='utf-8'))['points']
OUT = f'{RO}/datasets/grammar_examples.json'
SIDE = f'{RO}/reports/_p13_rebuild_manifest.json'

def strip(s):
    return re.sub(r'[\s（）()【】、，,；;：:"“”/•·]+', '', s or '')

# 停用泛词：会稀释匹配的类别名/空泛词（单字语法词如"会/能/了/的"保留）
STOP = {'名词', '动词', '代词', '副词', '量词', '助词', '介词', '数词', '叹词', '连词',
        '表示', '名词性', '动词性', '形容词性', '短语', '词语', '的1', '了1', '了2',
        '吧1', '呢1', '呢2', '呢3', '有（一）点儿'}

# no7z 按 level 组织
inst_by_lv = {lv: [x for x in no7z if x['level'] == lv] for lv in (1, 2, 3, 4)}

def grammar_vocab(p):
    """项目点 grammar 拆成的实词（含单字语法词，过滤空/泛词）"""
    gm = p.get('grammar', '') or ''
    words = [re.split(r'[、，,；;]', w)[0] for w in re.split(r'[、，,；;]', gm)]
    out = []
    for w in words:
        ws = strip(w)
        if ws and ws not in STOP:
            out.append(ws)
    return out

def find_match(syl_pt, lv):
    """返回 (matched_no7z_item, via, confidence)。以项目 grammar 实词为核心信号。"""
    lv_insts = inst_by_lv.get(lv, [])
    nm = strip(syl_pt.get('name', ''))
    vocab = grammar_vocab(syl_pt)
    best, best_via, best_conf = None, None, 0
    for cand in lv_insts:
        if cand.get('_used'):
            continue
        cl = strip(cand['label_full'] or cand['label'])
        cn = strip(cand['label'])
        score = 0
        # 信号1（核心）：项目 grammar 实词在 no7z label_full 里的命中
        for w in vocab:
            if w and w in cl:
                score += 2 if len(w) >= 2 else 1
        # 信号2：name 精确重叠
        if nm and len(nm) >= 2 and cn == nm:
            score += 4
        elif nm and len(nm) >= 4 and nm[:4] == cn[:4]:
            score += 2
        # 信号3：项目 grammar 实词连串前4字在 no7z label_full
        if vocab:
            joined = ''.join(k for k in vocab if k)
            if joined and joined[:4] in cl:
                score += 3
        if score > best_conf:
            best, best_via, best_conf = cand, 'score=%d' % score, score
    return best, best_via, best_conf

# 逐级逐点匹配
result_examples = {}
manifest = []
# 已知内容误配黑名单（no7z label 恰好包含 grammar 词子串但语义不对应的点）
MISMATCH_BLACKLIST = {'hsk30-g3-011': '被动句子串误配', 'hsk30-g3-030': '连词子串误配'}

for lv in (1, 2, 3, 4):
    lv_syl = [p for p in sy if re.match(rf'hsk30-g{lv}-\d+', p['id'])]
    lv_syl.sort(key=lambda x: int(re.match(r'hsk30-g\d-(\d+)', x['id']).group(1)))
    for p in lv_syl:
        cand, via, conf = find_match(p, lv)
        if p['id'] in MISMATCH_BLACKLIST:
            manifest.append({'sid': p['id'], 'sid_name': p.get('name',''),
                             'matches_no7z_id': None, 'no7z_label': None,
                             'via': 'REJECTED_BLACKLIST', 'count': 0})
            continue
        if cand and cand['examples'] and conf >= 2:
            # 语义正确性校验：必须存在 grammar 实词与 no7z label 的词重叠，否则视为误配丢弃
            gwords = [strip(w) for w in re.split(r'[、，,；;]', p.get('grammar','') or '') if strip(w)]
            nol = strip(cand['label_full'] or cand['label'])
            overlap = any(gw[:2] for gw in gwords if gw[:2] in nol)
            name_in = bool(strip(p.get('name',''))[:2]) and strip(p.get('name',''))[:2] in nol
            if not (overlap or name_in):
                manifest.append({'sid': p['id'], 'sid_name': p.get('name',''),
                                 'matches_no7z_id': None, 'no7z_label': None,
                                 'via': 'REJECTED_MISMATCH', 'count': 0})
                continue
            cand['_used'] = True
            result_examples[p['id']] = list(dict.fromkeys(cand['examples']))
            manifest.append({'sid': p['id'], 'sid_name': p.get('name',''),
                             'matches_no7z_id': cand['id'], 'no7z_label': cand['label'],
                             'via': via, 'count': len(cand['examples'])})
        else:
            manifest.append({'sid': p['id'], 'sid_name': p.get('name',''),
                             'matches_no7z_id': None, 'no7z_label': None,
                             'via': 'NO_MATCH', 'count': 0})

covered = len(result_examples)
from collections import Counter
by_lv = Counter(int(re.match(r'hsk30-g(\d)-', s).group(1)) for s in result_examples)
print(f"重建例句库：覆盖 {covered}/339  按级 {dict(sorted(by_lv.items()))}")

out_data = {
    "metadata": {
        "source": "no7z/hsk-sentences-audio (官方附录A OCR, CC-BY-SA)",
        "method": "内容级匹配到项目 hsk30-gN-XXX 考纲点（方案①收敛）",
        "coverage": {"total": 339, "covered": covered,
                     "levels": dict(sorted(by_lv.items()))},
        "rebuilt_at": "2026-09-10",
    },
    "examples": result_examples,
}
json.dump(out_data, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
json.dump(manifest, open(SIDE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print("已写:", OUT, "| 清单:", len(manifest), "条 →", SIDE)
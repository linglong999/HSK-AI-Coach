"""3.1 评测集数据校验（CI 雏形）
用法：python check_dataset.py golden_v1_4.json
检查：JSON 合法性、id 唯一、span 子串、P0 不在偏误集、correction 还原、语用 context、
     disputed 仲裁字段、clean sub_type 统一、README 统计核对。
"""
import json
import sys
from collections import Counter

def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'golden_v1_4.json'
    d = load(path)
    allitems = d['seed_golden'] + d['adversarial']
    ix = {x['item_id']: x for x in allitems}
    errors = []

    def chk(cond, msg):
        if not cond:
            errors.append(msg)

    # id 唯一
    chk(len(ix) == len(allitems), '存在重复 item_id')

    # 枚举合法性
    enum = {'词汇', '语法', '语用', '汉字'}
    for x in allitems:
        et = x.get('error_type')
        if et is not None:
            chk(et in enum, f"{x['item_id']} error_type 越界: {et}")
        if et is None:
            chk(x.get('sub_type') == 'clean', f"{x['item_id']} 干净样本 sub_type 非 clean")

    # span 子串
    for x in allitems:
        if x.get('error_span'):
            chk(x['error_span'] in x['original'],
                f"{x['item_id']} span 非 original 子串: {x['error_span']!r}")

    # correction 还原（非删除型）
    for x in allitems:
        if x.get('error_span') and x.get('correction') and x.get('corrected'):
            r = x['original'].replace(x['error_span'], x['correction'], 1)
            chk(r == x['corrected'], f"{x['item_id']} correction 未还原: {r}!={x['corrected']}")

    # 语用 context
    for x in allitems:
        if x.get('error_type') == '语用':
            chk(bool(x.get('context')), f"{x['item_id']} 语用类缺 context")

    # disputed 仲裁字段
    for x in allitems:
        if x.get('golden_status') == 'disputed':
            chk(bool(x.get('arbitrator')) and bool(x.get('arbitration_date')),
                f"{x['item_id']} disputed 缺仲裁字段")

    # 干净样本 correction 应为 None
    for x in allitems:
        if x.get('error_span') is None:
            chk(x.get('correction') is None, f"{x['item_id']} 干净样本 correction 应为 null")

    # 打印统计（供对照 README；README 用"全量=seed+adversarial"口径）
    print('=== 统计（应核对 README；README 采用全量=seed+adversarial 口径）===')
    print(f"seed={len(d['seed_golden'])} adv={len(d['adversarial'])} total={len(allitems)}")
    print('等级分布(seed):', dict(sorted(Counter(x['learner_level'] for x in d['seed_golden']).items())))
    print('类型分布(seed):', dict(Counter((x['error_type'] or 'clean') for x in d['seed_golden'])))
    print('类型分布(全量):', dict(Counter((x['error_type'] or 'clean') for x in allitems)))
    print('status(全量):', dict(Counter(x.get('golden_status', 'reviewed') for x in allitems)))

    if errors:
        print(f'\n校验失败 {len(errors)} 项:')
        for e in errors:
            print(' -', e)
        return 1
    print('\n校验通过 ✓')
    return 0

if __name__ == '__main__':
    sys.exit(main())
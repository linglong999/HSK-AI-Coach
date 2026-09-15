# ============================================================
# 从 krmanik/HSK-3.0 拉取 HSK 3.0 考纲语法 JSON（datasets/hsk30_raw/krmanik/）
# 运行：python scripts/fetch_syllabus.py（项目根 HSK-AI-Coach/ 下）
# 产物已随源码入库；删除后可由本脚本复现（见 .gitignore 注释）。
# ============================================================
# -*- coding: utf-8 -*-
import json
import os
import sys
import time
import urllib.parse
import urllib.request

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

DIR = os.path.join(_PROJECT_ROOT, 'datasets', 'hsk30_raw', 'krmanik')
DIRNAME = 'New HSK (2025)/HSK Grammar/json'
LEVELS = ['HSK 1', 'HSK 2', 'HSK 3', 'HSK 4', 'HSK 5', 'HSK 6', 'HSK 7-9']


def main():
    os.makedirs(DIR, exist_ok=True)

    result = {}
    for lv in LEVELS:
        fn = lv.replace(' ', '_') + '.json'
        url = 'https://raw.githubusercontent.com/krmanik/HSK-3.0/main/' + \
              urllib.parse.quote(DIRNAME + '/' + lv + '.json')
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'trae'})
                data = urllib.request.urlopen(req, timeout=30).read()
                if len(data) < 50:
                    raise ValueError('empty body')
                with open(os.path.join(DIR, fn), 'wb') as f:
                    f.write(data)
                arr = json.loads(data)
                result[lv] = len(arr)
                break
            except Exception as e:
                time.sleep(1.5)
                if attempt == 2:
                    print('FAIL', lv, e)

    print('KV 条数统计:', json.dumps(result, ensure_ascii=False))
    print('合计:', sum(result.values()))

    # 7-9 行内级别验证 + 细目空统计
    g79 = json.load(open(os.path.join(DIR, 'HSK_7-9.json'), encoding='utf-8'))
    print('g79 首条:', json.dumps(g79[0], ensure_ascii=False))
    print('g79 keys:', list(g79[0].keys()))
    keys = set()
    for lv in LEVELS:
        arr = json.load(open(os.path.join(DIR, lv.replace(' ', '_') + '.json'), encoding='utf-8'))
        for it in arr:
            keys.update(it.keys())
    print('全部字段并集:', sorted(keys))
    # 细目空计数
    empty_xiang = 0
    for lv in LEVELS:
        arr = json.load(open(os.path.join(DIR, lv.replace(' ', '_') + '.json'), encoding='utf-8'))
        for it in arr:
            if not (it.get('细目') or '').strip():
                empty_xiang += 1
    print('细目为空总条数:', empty_xiang)


if __name__ == '__main__':
    main()

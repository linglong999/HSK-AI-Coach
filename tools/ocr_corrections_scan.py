# ============================================================
# tools/ocr_corrections_scan.py · P0.7 OCR 校正数据实测采集
# 用真实 OCR 引擎（RapidOCR）扫 HSK30 大纲「认读字」表，逐字识别，
# 收集真实"误读→正字"对，写入 datasets/ocr_corrections/ 供 RAG 摄入。
# - 逐字放大渲染直击识别组件，规避印刷表格读序错位。
# - 续页继承上一组等级，补全 level 标签。
# 用法：python tools/ocr_corrections_scan.py
# ============================================================
import io, json, os, re, sys
import pymupdf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.ocr import _load_ocr, _image_bytes_to_text  # noqa: E402

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF = os.path.join(PROJECT, 'datasets', 'hsk30_raw', 'HSK30-大纲-词汇汉字语法.pdf')
OUT = os.path.join(PROJECT, 'datasets', 'ocr_corrections', 'ocr_corrections_real.json')

ROW = re.compile(r'^(\d+)\s*\.\s*([\u4e00-\u9fff])$')
LVL = re.compile(r'([一二三四五六七八九—]{1,6})级')
CJK = re.compile(r'[\u4e00-\u9fff]')


def _font():
    for c in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simsun.ttc',
              r'C:\Windows\Fonts\simhei.ttf'):
        if os.path.exists(c):
            return c
    raise SystemExit('未找到中文字体')


def render_char(ch, size=160):
    """单字 → 白底 PNG 字节。"""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new('RGB', (size, size), 'white')
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype(_font(), int(size * 0.72))
    except Exception:  # noqa: BLE001
        f = ImageFont.load_default(size=int(size * 0.72))
    bbox = d.textbbox((0, 0), ch, font=f)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1]), ch,
           fill='black', font=f)
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


def main():
    # 1) 收集认读字（去重）+ 等级（续页继承）
    doc = pymupdf.open(PDF)
    char2level, order, level = {}, [], None
    for i in range(doc.page_count):
        t = (doc[i].get_text() or '')
        m = LVL.search(t)
        if m:
            level = m.group(1) + '级'
        for ln in t.split('\n'):
            rm = ROW.match(ln.strip())
            if rm:
                ch = rm.group(2)
                if ch not in char2level:
                    order.append(ch)
                    char2level[ch] = level or '0'
    doc.close()

    # 2) 逐字 OCR
    eng = _load_ocr()
    pairs, seen, skipped = [], set(), 0
    for idx, ch in enumerate(order):
        if idx and idx % 200 == 0:
            print('  progress %d/%d pairs=%d' % (idx, len(order), len(pairs)), flush=True)
        txt = _image_bytes_to_text(eng, render_char(ch)).strip()
        hit = [c for c in txt if CJK.fullmatch(c)]
        if not hit:
            skipped += 1
            continue
        if len(hit) == 1 and hit[0] != ch:
            k = (hit[0], ch)
            if k not in seen:
                seen.add(k)
                pairs.append({'original': hit[0], 'corrected': ch,
                              'level': char2level[ch],
                              '依据': 'HSK30认读字真实OCR实测（RapidOCR逐字识别）校正'})

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(pairs, f, ensure_ascii=False, indent=2)
    print('OCR 空检(跳过):', skipped)
    print('误读对总数:', len(pairs))
    from collections import Counter
    print('等级分布:', dict(Counter(p['level'] for p in pairs)))
    print('已写:', OUT)


if __name__ == '__main__':
    main()
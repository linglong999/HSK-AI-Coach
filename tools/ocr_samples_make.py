# ============================================================
# tools/ocr_samples_make.py · P0.7 固定样例图片集
# 把 HSK30 大纲固定页（清晰印刷体）渲染成 PNG，作为 OCR 端到端验收样例集。
# 样例为"课本照片"的替身：清晰印刷体、含中文词汇/认读字，正是 OCR 的目标场景。
# 用法：python tools/ocr_samples_make.py
# ============================================================
import os, sys
import pymupdf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.ocr import OCR_DPI  # noqa: E402

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF = os.path.join(PROJECT, 'datasets', 'hsk30_raw', 'HSK30-大纲-词汇汉字语法.pdf')
OUT = os.path.join(PROJECT, 'tests', 'data', 'ocr_samples')

# （页码, 标签）——固定样例：词汇一级/二级 + 一级认读字
SAMPLES = [(2, 'vocab_l1'), (8, 'vocab_l2'), (279, 'chars_l1')]

def main():
    os.makedirs(OUT, exist_ok=True)
    doc = pymupdf.open(PDF)
    for page, label in SAMPLES:
        pix = doc[page].get_pixmap(dpi=OCR_DPI)
        path = os.path.join(OUT, f'{label}.png')
        pix.save(path)
        print('已生成', path, pix.width, 'x', pix.height)
    doc.close()

if __name__ == '__main__':
    main()
# ============================================================
# engine/ocr.py · P0.7 OCR + RAG
# 职责单一：把"图片 / PDF 页"转成干净文本（OCR 只管识字）。
# - 偏误识别仍由 engine/recognizer.py 做，两步解耦不混（v1 P0.7 步骤2/3）
# - RapidOCR 属例外依赖：懒加载（函数内 import），不进运行时零依赖核心
# - 未安装 / 加载失败 → 抛 OcrUnavailable，调用方优雅降级为"不支持图片"
# ============================================================

import io
import os
from typing import List, Optional

MAX_PDF_PAGES = 10        # 单次 OCR 的 PDF 页数上限（批量场景防成本失控）
OCR_DPI = 200             # PDF 页渲染分辨率（历史稿 150 DPI；教材印刷体 200 更稳）


class OcrUnavailable(RuntimeError):
    """OCR 能力缺失（RapidOCR 未装 / 加载失败 / 纯文本源错误）。"""


def _load_ocr():
    """懒加载 RapidOCR。失败抛 OcrUnavailable，不污染运行时核心。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR()
    except Exception as e:  # noqa: BLE001
        raise OcrUnavailable(f"RapidOCR 不可用：{e}") from e


def _rar_png_bytes(pixmap) -> Optional[bytes]:
    """把 PyMuPDF pixmap 编码为 PNG 字节；失败返回 None（该页跳过）。"""
    try:
        return pixmap.tobytes("png")
    except Exception:  # noqa: BLE001
        return None


def _images_from_pdf(pdf_bytes: bytes, max_pages: int = MAX_PDF_PAGES):
    """PDF 字节 → 页 PNG 字节序列。pymupdf 懒加载（同为例外依赖）。"""
    try:
        import pymupdf  # noqa: PLC0415 懒加载，保持运行时核心零依赖
    except Exception as e:  # noqa: BLE001
        raise OcrUnavailable(f"pymupdf 不可用（PDF→图需它）：{e}") from e
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        raise OcrUnavailable(f"PDF 打开失败：{e}") from e
    out = []
    for i in range(min(doc.page_count, max_pages)):
        pix = doc[i].get_pixmap(dpi=OCR_DPI)
        data = _rar_png_bytes(pix)
        if data:
            out.append(data)
    doc.close()
    return out


def _image_bytes_to_text(engine, image_bytes: bytes) -> str:
    """单图 → 干净文本。识别不到文字返回空串。"""
    import numpy as np
    from PIL import Image
    try:
        pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        arr = np.asarray(pil)
    except Exception as e:  # noqa: BLE001
        raise OcrUnavailable(f"图片解码失败：{e}") from e
    result = engine(arr)
    # rapidocr result[0] = [[box, text, score], ...]（每条一个识别行）
    items = result[0] if result else None
    if not items:
        return ""
    texts = []
    for it in items:
        if isinstance(it, (list, tuple)) and len(it) >= 2 and it[1]:
            texts.append(str(it[1]).strip().replace("\t", " "))
    texts = [x for x in texts if x]
    return "\n".join(texts)


def extract_text_from_images(image_bytes_list: List[bytes],
                             keep_layout: bool = False) -> str:
    """多张图 → 拼接文本。图与图之间用分隔空行（keep_layout 透传后续）。"""
    if not image_bytes_list:
        return ""
    engine = _load_ocr()
    blocks = []
    for b in image_bytes_list:
        try:
            t = _image_bytes_to_text(engine, b)
        except OcrUnavailable:
            raise
        if t:
            blocks.append(t)
    sep = "\n\n" if keep_layout else "\n"
    return sep.join(blocks)


def extract_text_from_pdf(pdf_bytes: bytes,
                          max_pages: int = MAX_PDF_PAGES) -> str:
    """PDF → 各页文本，页间以空行分隔。纯文本 PDF（无可渲染图）→ 空串。"""
    images = _images_from_pdf(pdf_bytes, max_pages)
    return extract_text_from_images(images)
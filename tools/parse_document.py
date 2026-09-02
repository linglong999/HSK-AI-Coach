# ============================================================
# tools/parse_document.py
# M9 · 文档解析路由（本地文本/简单文档；复杂走 provider 位默认关）
# 对齐 OpenMAIC lib/document/*：输出归一化块结构（blocks[]，无 providerRaw）
# ============================================================

import html
from typing import Any, Dict, Optional

DOMAIN = "parse_document"
_MAX_CHARS = 200_000  # 防超大输入


def parse_text(content: str) -> Dict[str, Any]:
    """本地解析纯文本（UTF-8 原样）→ 块结构。"""
    if len(content) > _MAX_CHARS:
        content = content[:_MAX_CHARS]
    return {"ok": True, "blocks": [{"type": "text", "text": content}]}


def parse_markdown(content: str) -> Dict[str, Any]:
    """本地解析 Markdown → markdown 块。"""
    if len(content) > _MAX_CHARS:
        content = content[:_MAX_CHARS]
    return {"ok": True, "blocks": [{"type": "markdown", "text": content}]}


def parse_simple_html(content: str) -> Dict[str, Any]:
    """简单 HTML：剥标签取文本（仅标准库 html.parser，不引入 bs4）。"""
    import re
    from html.parser import HTMLParser

    class _TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
            self._skip = 0
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self._skip += 1
        def handle_endtag(self, tag):
            if tag in ("script", "style") and self._skip:
                self._skip -= 1
        def handle_data(self, data):
            if not self._skip:
                self.parts.append(data)

    p = _TextExtractor()
    p.feed(content)
    text = re.sub(r"\s+", " ", " ".join(p.parts)).strip()
    return {"ok": True, "blocks": [{"type": "text", "text": text[: _MAX_CHARS]}],
            "format": "html"}


def parse(params: Optional[Dict[str, Any]], provider_id: str = "local",
          **_: Any) -> Dict[str, Any]:
    """文档解析路由。text/markdown/html 本地；未知/复杂 → not_configured。
    params：{text, path(可选), format: text|markdown|html}"""
    params = params or {}
    raw = params.get("text")
    if raw is None:
        return {"ok": False, "status": "error", "message": "缺少 text 参数"}
    fmt = (params.get("format") or "text").lower().strip()
    if fmt == "html":
        return parse_simple_html(str(raw))
    if fmt == "markdown":
        return parse_markdown(str(raw))
    # 未知格式（如 pdf/office）→ 复杂文档 provider 位，默认关
    if fmt in ("pdf", "docx", "doc", "pptx", "xlsx"):
        return {"ok": False, "status": "not_configured",
                "message": f"格式 '{fmt}' 需外部 provider，当前默认关（仅支持 text/markdown/html）"}
    return parse_text(str(raw))
"""HTTP 层横切传输工具（serve.py 下沉）。

只做纯 HTTP 传输，不碰业务状态：
- send_json：写 JSON 响应 + CORS + 可选游客 vid cookie（P0.10）
- send_error_json：统一异常包装（保留 traceback 于日志，响应只返 message）
- send_static：web/ 静态托管，含路径穿越防护 + no-store

这些函数以 handler 实例为第一参数（调用方是 BaseHTTPRequestHandler 子类）。
"""

import json
import os


def send_json(h, obj, status=200):
    """写 JSON 响应。访问见 **Allow-Origin: ***（本地开发，不做鉴权）。"""
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Access-Control-Allow-Origin", "*")
    # P0.10 游客 vid 下发（dialog 闸门暂存）
    vid_cookie = getattr(h, "_vid_cookie", None)
    if vid_cookie:
        h.send_header("Set-Cookie", vid_cookie)
    h.end_headers()
    h.wfile.write(body)


def send_error_json(h, status, message):
    """统一异常包装：响应只返 message，异常 repr 打到服务日志（可观测）。"""
    if h is not None:
        try:
            h.log_message("[serve] error proto: %s", message)
        except Exception:  # noqa: BLE001 日志失败不影响返回
            pass
    send_json(h, {"error": message}, status)


def send_static(h, index_dir, rel):
    """web/ 静态托管。防路径穿越：只允许 index_dir 下的资源。"""
    base = os.path.realpath(index_dir)
    path = os.path.realpath(os.path.join(base, rel.lstrip("/")))
    if not path.startswith(base) or not os.path.isfile(path):
        h.send_error(404, "not found")
        return
    ctype = "text/html; charset=utf-8"
    if path.endswith(".js"):
        ctype = "application/javascript; charset=utf-8"
    elif path.endswith(".css"):
        ctype = "text/css; charset=utf-8"
    elif path.endswith(".svg"):
        ctype = "image/svg+xml"
    with open(path, "rb") as f:
        body = f.read()
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")  # 本地开发改前端即生效
    h.end_headers()
    h.wfile.write(body)
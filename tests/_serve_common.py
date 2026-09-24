"""test_serve* 共享的 live-server 启动 helper。

M0-C6：6 个 test_serve*.py 从 ThreadingHTTPServer 迁移到 uvicorn（服务 FastAPI app）。
只替换 server 启动/回收段，http.client 断言体零改动。
"""
import socket
import threading
import time

import uvicorn


def make_server(app, tmp=None):
    """绑定随机空闲端口 → 后台起 uvicorn 服务 app。

    返回 (server, thr, port)：tearDown 调用 stop_server(server, thr)。
    等待 server.started 就绪再返回，保证对端已可连。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)
    thr = threading.Thread(target=server.run, daemon=True)
    thr.start()
    deadline = time.time() + 10
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn 启动超时")
        time.sleep(0.01)
    return server, thr, port


def stop_server(server, thr):
    """发 should_exit 信号并 join 后台线程（ba用 timeout 防卡死测试进程）。"""
    server.should_exit = True
    thr.join(timeout=10)


def handle_response(port, method, path, body_bytes, headers=None):
    """发一个 HTTP 请求，返回 (status, raw_bytes, resp_headers)。
    供各 test_serve* 的 _post 复用（B7 S1 之后 /api/dialog 为 SSE，需按 Content-Type 分派）。"""
    import http.client
    h = dict(headers or {})
    h.setdefault("Content-Type", "application/json")
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body_bytes, h)
    r = c.getresponse()
    raw = r.read()
    hs = dict(r.getheaders())
    c.close()
    return r.status, raw, hs


def reassemble_dialog(raw: str) -> dict:
    """B7 S1：把 /api/dialog 的 SSE 事件流重汇编回原 JSON 响应 dict。
    done.data 携带完整响应（含 trace/conversation_id），取 done 即还原全程；
    message/intercept 事件为流式增量（契约形状由 S1 契约测试单独断言）。"""
    import json
    for block in raw.split("\n\n"):
        ev = data = None
        for ln in block.strip().split("\n"):
            if ln.startswith("event:"):
                ev = ln[len("event:"):].strip()
            elif ln.startswith("data:"):
                data = ln[len("data:"):].strip()
        if ev == "done" and data:
            return json.loads(data)
    return {}


def parse_body(path, raw_text):
    """测试 `_post` 通用响应解析：/api/dialog 的 SSE 重汇编；其余按 JSON。
    B7 S1 后 /api/dialog 为 SSE（Content-Type: text/event-stream、文本以 event: 开头）。"""
    import json
    text = (raw_text or "").strip()
    if path == "/api/dialog" and text.startswith("event:"):
        return reassemble_dialog(text)
    try:
        return json.loads(text)
    except Exception:
        return None
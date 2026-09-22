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
# ============================================================
# C5 · SSE 最小事件流冒烟（M0 纪律：产品端点零新增，纯测试承载路由）
# 覆盖：
#   - EventSourceResponse + async 生成器：分块 message×2 → done 事件，含 event 字段
#   - TestClient 消费点断言帧序（event message → message → done，data 逐字）
#   - 断连清理：客户端提前断开 → 生成器抛 asyncio.CancelledError → 捕获置清理标记
# 只验传输层（sse-starlette）行为；真实 /api/dialog 流归 B7 S1。
# 运行: python -m unittest tests.test_sse_smoke -v
# ============================================================

import asyncio
import socket
import threading
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sse_starlette.sse import EventSourceResponse

from ._serve_common import make_server, stop_server


def _build_app(on_cancel: threading.Event):
    app = FastAPI()

    @app.get("/sse")
    async def sse():
        async def gen():
            try:
                yield {"event": "message", "data": "part-1"}
                await asyncio.sleep(0.5)   # 给客户端足够窗口断开 TCP
                yield {"event": "message", "data": "part-2"}
                yield {"event": "done", "data": "fin"}
            except asyncio.CancelledError:
                on_cancel.set()   # 断连清理路径：客户端断开 → sse 取消生成器
                raise

        return EventSourceResponse(gen())

    return app


class SseSmokeTest(unittest.TestCase):

    def test_frame_order_and_event_field(self):
        on_cancel = threading.Event()
        client = TestClient(_build_app(on_cancel))   # noqa: S108
        with client.stream("GET", "/sse") as r:
            self.assertEqual(r.status_code, 200)
            self.assertIn("text/event-stream", r.headers.get("content-type", ""))
            frames = {"message": [], "done": []}
            for line in r.iter_lines():
                line = (line or "").strip()
                if line.startswith("event:"):
                    cur = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    frames[cur].append(line.split(":", 1)[1].strip())
        self.assertEqual(frames["message"], ["part-1", "part-2"])
        self.assertEqual(frames["done"], ["fin"])

    def test_disconnect_triggers_cancelled_cleanup(self):
        # TestClient 会缓冲整个响应、不产生真实 TCP 断连，故用真 uvicorn 服务器 + 裸 socket。
        on_cancel = threading.Event()
        app = _build_app(on_cancel)
        server, thr, port = make_server(app)
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            with s:
                s.sendall(b"GET /sse HTTP/1.1\r\nHost: x\r\n\r\n")
                buf = b""
                while b"data: part-1" not in buf:
                    buf += s.recv(4096)
                    if not buf:
                        break
            # 连接已关闭（with s 已退出）→ server 端收到 http.disconnect → 取消生成器
        finally:
            stop_server(server, thr)
        self.assertTrue(on_cancel.wait(3.0),
                        "客户端断开后生成器未被取消（CancelledError 清理未生效）")

    def test_stream_terminates_after_done(self):
        on_cancel = threading.Event()
        client = TestClient(_build_app(on_cancel))   # noqa: S108
        with client.stream("GET", "/sse") as r:
            lines = [ln for ln in r.iter_lines() if ln and ln.strip()]
        self.assertEqual(
            [ln for ln in lines if ln.startswith("event:")],
            ["event: message", "event: message", "event: done"])
        # 正常结束（done 后不再产生）不会触发断连清理标记
        self.assertFalse(on_cancel.is_set())


if __name__ == "__main__":
    unittest.main()
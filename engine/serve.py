# ============================================================
# 前端壳 HTTP 服务层（M10）
# 零第三方依赖：仅 Python 标准库 http.server。
# 暴露四类入口给前端壳（原生 JS+SVG）：
#   POST /api/process   → Router.process(text) → 契约 v1 result（JSON-接缝契约-v1.md）
#   POST /api/verify    → Router.verify_rephrase() → 复述验证（契约 verify_rephrase 节）
#   GET  /api/graph     → ErrorGraph.graph_snapshot()（nodes/edges/queue）
#   GET  /              → 托管 前端壳 index.html
# 启动: python -m engine.serve [--port 8612] [--host 127.0.0.1] [--learner demo]
# 免 Key 亦可启动：process 走 4.2 规则回退降级，契约 degraded[] 记录；图谱照常返回。
# ============================================================

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.router import Router

# 前端壳静态目录：serve.py 位于 engine/，前端壳在项目根前端/ 或 web/
_INDEX_DIR = os.path.join(_PROJECT_ROOT, "web")


def make_handler(router: Router, index_dir: str):
    """工厂构造 handler，闭包捕获 Router（每个请求共享同一图谱实例）。
    ThreadingHTTPServer 每请求一线程 → 用一把锁串行化整个闭环
    （process/verify 是多步读写组合，仅靠图谱内部锁防不住交错）。"""

    lock = threading.RLock()

    class H(BaseHTTPRequestHandler):
        _router = router
        _index_dir = index_dir
        _lock = lock

        def log_message(self, fmt, *args):
            sys.stderr.write("  [serve] " + fmt % args + "\n")

        def _send_json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, rel: str):
            # 防路径穿越：只允许 web/ 下的静态资源
            base = os.path.realpath(self._index_dir)
            path = os.path.realpath(os.path.join(base, rel.lstrip("/")))
            if not path.startswith(base) or not os.path.isfile(path):
                self.send_error(404, "not found")
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
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")  # 本地开发改前端即生效
            self.end_headers()
            self.wfile.write(body)

        def _do_api_process(self, payload: dict):
            text = str((payload.get("text") or "")).strip()
            if not text:
                self._send_json({"error": "empty text"}, 400)
                return
            with self._lock:
                # 同一 Router 实例 → 图谱随学习者在服务内持久累积
                result = self._router.process(text)
                # 补充图谱快照，供前端一次性渲染（无需二次 GET）
                result["graph"] = self._router.graph.graph_snapshot()
            self._send_json(result)

        def _do_api_verify(self, payload: dict):
            # 契约：key_points 是复述验证唯一点来源（前端从讲解卡回传）
            restatement = str((payload.get("restatement") or "")).strip()
            key_points = payload.get("key_points") or []
            explanation = str(payload.get("explanation") or "")
            if not restatement:
                self._send_json({"error": "empty restatement"}, 400)
                return
            with self._lock:
                out = self._router.verify_rephrase(
                    explanation, key_points, restatement,
                    event_key=f"web#{restatement}")  # 稳定 key：实体本身（原则4）
            self._send_json(out)

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            if path == "/api/process":
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    raw = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw or b"{}")
                except Exception:
                    self._send_json({"error": "invalid json body"}, 400)
                    return
                try:
                    self._do_api_process(payload)
                except Exception as e:
                    self._send_json({"error": f"process failed: {e}"}, 500)
                return
            if path == "/api/verify":
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    raw = self.rfile.read(length) if length else b"{}"
                    payload = json.loads(raw or b"{}")
                except Exception:
                    self._send_json({"error": "invalid json body"}, 400)
                    return
                try:
                    self._do_api_verify(payload)
                except Exception as e:
                    # 验证引擎无规则回退（2.3）：如实报错，不伪造 verdict
                    self._send_json({"error": f"verify failed: {e}"}, 500)
                return
            self.send_error(404)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path.rstrip("/") == "/api/graph":
                with self._lock:
                    snap = self._router.graph.graph_snapshot()
                self._send_json(snap)
                return
            # 静态：根 → index.html
            rel = parsed.path or "/"
            if rel == "/" or rel == "":
                rel = "/index.html"
            self._send_static(rel)

    return H


def main():
    ap = argparse.ArgumentParser(description="HSK-AI-Coach 前端壳服务（零依赖）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8612)
    ap.add_argument("--learner", default="demo", help="学习者 id → data/graph_<id>.json")
    args = ap.parse_args()

    router = Router(learner_id=args.learner, native_lang="英语", user_level="HSK3")
    if not os.path.isdir(_INDEX_DIR):
        print(f"[serve] 前端壳目录不存在：{_INDEX_DIR}")
        print("[serve] 将仅提供 API（/api/process /api/graph），静态页待 M10 生成 web/index.html")
    httpd = ThreadingHTTPServer((args.host, args.port), make_handler(router, _INDEX_DIR))
    print(f"HSK-AI-Coach 前端壳：http://{args.host}:{args.port}  (learner={args.learner})")
    print("  POST /api/process   纠错闭环（契约 v1 JSON）")
    print("  POST /api/verify    复述验证（key_points + restatement）")
    print("  GET  /api/graph     图谱 nodes/edges/queue")
    print("  GET  /              前端壳页面（若 web/index.html 存在）")
    print("  Ctrl+C 停止")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] 停止")
        httpd.server_close()


if __name__ == "__main__":
    main()
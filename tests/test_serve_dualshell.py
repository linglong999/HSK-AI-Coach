# ============================================================
# tests/test_serve_dualshell.py
# B7 S4 · 双壳并存静态托管
# 覆盖：
#   A. /  → React 壳（index_dir；有 dist 子目录则用构建产物）
#   B. /legacy/ → legacy 工具壳（独立目录，平移后的旧 HTML）
#   C. 13 条 API 路由契约零变化（双壳挂载不影响 /api/*——既有 serve 测试全绿即证）
# 对齐验收：双壳静态托管路由测试；legacy 13 条路由零契约变化。
# 运行: python -m unittest tests.test_serve_dualshell -v
# ============================================================

import http.client
import os
import sys
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tests._serve_common import make_server, stop_server
from engine.serve import create_app


class Rec:
    def recognize(self, text, level=3, native_lang=""):
        return {"errors": [], "uncertain": [], "degraded": []}


class Router:
    learner_id = "dualshell_user"
    native_lang = "zh"

    def __init__(self):
        from engine.graph.error_graph import ErrorGraph
        self.graph = ErrorGraph("dualshell_test")
        self.recognizer = Rec()


def _build(root_dir, legacy_dir):
    app = create_app(Router(), root_dir, memory_root=root_dir,
                     gate=None, legacy_dir=legacy_dir)
    return make_server(app)


def _get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    raw = r.read().decode()
    st, loc = r.status, r.getheader("Location")
    c.close()
    return st, raw, loc


class DualShellTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        root = os.path.join(self.tmp, "web")
        leg = os.path.join(self.tmp, "web_legacy")
        os.makedirs(root, exist_ok=True)
        os.makedirs(leg, exist_ok=True)
        with open(os.path.join(root, "index.html"), "w", encoding="utf-8") as f:
            f.write("<html>REACT-SHELL</html>")
        with open(os.path.join(leg, "index.html"), "w", encoding="utf-8") as f:
            f.write("<html>LEGACY-SHELL</html>")
        with open(os.path.join(leg, "metrics.html"), "w", encoding="utf-8") as f:
            f.write("<html>LEGACY-METRICS</html>")
        self.server, self.thr, self.port = _build(root, leg)

    def tearDown(self):
        stop_server(self.server, self.thr)

    def test_root_serves_react_shell(self):
        st, raw, _ = _get(self.port, "/")
        self.assertEqual(st, 200)
        self.assertIn("REACT-SHELL", raw)

    def test_legacy_serves_legacy_shell(self):
        st, raw, _ = _get(self.port, "/legacy/")
        self.assertEqual(st, 200)
        self.assertIn("LEGACY-SHELL", raw)

    def test_legacy_serves_metrics(self):
        st, raw, _ = _get(self.port, "/legacy/metrics.html")
        self.assertEqual(st, 200)
        self.assertIn("LEGACY-METRICS", raw)

    def test_dist_build_artifact_preferred(self):
        # / 有 dist 子目录 → 优先服务构建产物
        dist = os.path.join(self.tmp, "web", "dist")
        os.makedirs(dist, exist_ok=True)
        with open(os.path.join(dist, "index.html"), "w", encoding="utf-8") as f:
            f.write("<html>REACT-DIST</html>")
        s, t, p = _build(os.path.join(self.tmp, "web"),
                         os.path.join(self.tmp, "web_legacy"))
        try:
            st, raw, _ = _get(p, "/")
            self.assertEqual(st, 200)
            self.assertIn("REACT-DIST", raw)
        finally:
            stop_server(s, t)


if __name__ == "__main__":
    unittest.main()
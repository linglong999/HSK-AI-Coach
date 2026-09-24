# ============================================================
# tests/test_web_deps.py
# B7 S4 · React 壳依赖边界断言
# 验收 1243："React 壳不引 UI 组件库/图表库（package.json 依赖断言）"。
#   手写轻组件 + 原生 CSS（不引 MUI/AntD/图表库），防依赖蔓延。
# 纯文件读取零网络/零构建；package.json 缺席（web/ 未建）→ 断言 pass（可被
#   CI 前端 build step 单独值守，本用例只作边界红线）。
# 运行: python -m unittest tests.test_web_deps -v
# ============================================================

import json
import os
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = os.path.join(_PROJECT_ROOT, "web", "package.json")

# 禁区：UI 组件库 / 图表库（B7-A 聚焦 5"静态表格/卡片/进度条"原则延伸到 UI 库）
_FORBIDDEN = {
    "antd", "@mui", "@material-ui", "material-ui", "element-ui",
    "vant", "zarm", "react-bootstrap", "bootstrap",
    "echarts", "chart.js", "recharts", "d3", "@visx",
    "@ant-design/charts", "nivo", "plotly.js", "highcharts",
}


class WebDepsTest(unittest.TestCase):
    def test_package_json_exists(self):
        self.assertTrue(os.path.isfile(_PKG),
                        f"web/package.json 缺失：{_PKG}")

    def test_no_ui_or_chart_library(self):
        if not os.path.isfile(_PKG):
            self.skipTest("web/package.json 不存在，跳过依赖断言")
        with open(_PKG, encoding="utf-8") as f:
            pkg = json.load(f)
        deps = dict(pkg.get("dependencies", {}))
        deps.update(pkg.get("devDependencies", {}))
        raises = sorted(n for n in deps if _is_forbidden(n))
        self.assertEqual(raises, [],
                         f"React 壳引用了 UI/图表库：{raises}（不引 UI 库/图表库红线）")


def _is_forbidden(name: str) -> bool:
    n = name.lower()
    return any(n == f or n.startswith(f) for f in _FORBIDDEN)


if __name__ == "__main__":
    unittest.main()
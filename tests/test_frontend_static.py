# ============================================================
# 前端壳静态验收（0.22 方向3 热修沉淀）
# 背景：i18n 的 applyLang 用 textContent 覆写 [data-i18n] 元素，
#   会杀掉其内部子元素 —— scenePick 按钮内的 <span id="scenePickName">
#   被干掉后 renderScenePick 抛 TypeError，场景点击链静默瘫痪
#   （后端测试全绿、JS 语法检查全过，只有真实点击才炸）。
# 锁两条静态不变量，防止同类回归：
#   1. 任何 [data-i18n] 元素内部不得含子元素（叶子节点原则）
#   2. JS $() 引用的元素 id 必须在 HTML 中存在（含 data-i18n 杀掉场景）
# 运行: python -m unittest tests.test_frontend_static -v
# ============================================================

import os
import re
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INDEX = os.path.join(_PROJECT_ROOT, "web", "index.html")


def _load():
    with open(_INDEX, encoding="utf-8") as f:
        return f.read()


class DataI18nLeafTest(unittest.TestCase):
    """不变量 1：[data-i18n] 元素必须是叶子（无子标签）。"""

    def test_data_i18n_elements_have_no_children(self):
        html = _load()
        body = re.sub(r"<script>.*?</script>", "", html, flags=re.S)
        offenders = []
        for m in re.finditer(r"<(\w+)([^>]*\bdata-i18n=[^>]*)>", body):
            tag, attrs = m.group(1), m.group(2)
            if tag.lower() in ("input", "textarea", "img", "br", "hr"):
                continue  # 空元素无内容可杀
            end = body.find(f"</{tag}>", m.end())
            content = body[m.end():end] if end > 0 else ""
            if re.search(r"<\w+", content):
                offenders.append(f"<{tag} {attrs.strip()[:70]}> 内含子元素")
        self.assertEqual(
            offenders, [],
            "applyLang 会用 textContent 覆写 [data-i18n] 元素，杀掉内部子元素（0.22 场景按钮踩坑）：\n"
            + "\n".join(offenders))


class JsIdReferenceTest(unittest.TestCase):
    """不变量 2：JS $() 引用的 id 必须存在于 HTML（动态创建的除外）。"""

    DYNAMIC_IDS = {"gRoot"}  # renderMap 运行时创建，且消费方带空值守卫

    def test_js_id_refs_exist_in_html(self):
        html = _load()
        m = re.search(r"<script>(.*?)</script>", html, re.S)
        self.assertIsNotNone(m, "未找到 <script> 块")
        js = m.group(1)
        ids_in_html = set(re.findall(r'id="([A-Za-z0-9_\-]+)"', html))
        used = set(re.findall(r"\$\('([A-Za-z0-9_\-]+)'\)", js))
        missing = sorted(used - ids_in_html - self.DYNAMIC_IDS)
        self.assertEqual(
            missing, [],
            f"JS 引用了 HTML 中不存在的元素 id（运行时 $() 返回 null，链式调用即抛错）：{missing}")


class I18nParityTest(unittest.TestCase):
    """不变量 3（0.23）：I18N.en 与 I18N.zh 键集合必须一致。
    双语键是手工双份维护的，漏一边时 t() 直接把键名渲染给用户
    （或静默回退英文），单测/语法检查都发现不了。"""

    _STR = re.compile(r"'(?:[^'\\]|\\.)*'")  # 先剥离字符串字面量，值内的冒号不参与提键

    @classmethod
    def _keys(cls, block):
        stripped = cls._STR.sub("", block)
        return set(re.findall(r"([A-Za-z0-9_]+)\s*:", stripped))

    def test_en_zh_keys_identical(self):
        html = _load()
        m = re.search(r"const I18N=\{\s*en:\{(.*?)\n\s*zh:\{(.*?)\n\};", html, re.S)
        self.assertIsNotNone(m, "未找到 I18N en/zh 字典结构")
        en, zh = self._keys(m.group(1)), self._keys(m.group(2))
        only_en, only_zh = sorted(en - zh), sorted(zh - en)
        self.assertEqual(
            (only_en, only_zh), ([], []),
            f"i18n 双语键不对齐（缺哪边就补哪边）：only_en={only_en} only_zh={only_zh}")


if __name__ == "__main__":
    unittest.main()

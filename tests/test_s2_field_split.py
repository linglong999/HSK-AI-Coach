# ============================================================
# tests/test_s2_field_split.py
# v0.3 P0.1 S2 拆分验证：learner_l1 与 ui_lang 两个字段语义分离
# 覆盖：
#   - _learner_l1_is_zh：与 _l1_is_zh 语义完全独立（UI 不混入 L1 判定）
#   - _learner_l1_is_zh：缺省 / "unknown" 视为 False（不污染 L1 统计）
#   - build_persona_brief：新签名 (learner_l1 kwarg) 兼容旧调用
#   - build_persona_brief：learner_l1 字段不改变措辞（C4 类 L1 个性化后续再做）
# 运行: python -m unittest tests.test_s2_field_split -v
# ============================================================

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.persona import (
    _l1_is_zh, _learner_l1_is_zh, build_persona_brief,
    REPLY_STYLES, DEFAULT_PERSONA)


class LearnerL1IsZhTest(unittest.TestCase):
    """_learner_l1_is_zh 仅判定学习者母语，与 UI 语言无关。"""

    def test_chinese_variants(self):
        for v in ("zh", "中文", "汉语", "chinese", "zh-cn", "zh-hans"):
            self.assertTrue(_learner_l1_is_zh(v),
                            msg=f"应当识别为中文母语: {v!r}")

    def test_non_chinese(self):
        for v in ("en", "ja", "ko", "es", "fr", "ru", "ar"):
            self.assertFalse(_learner_l1_is_zh(v),
                             msg=f"不应当识别为中文母语: {v!r}")

    def test_empty_and_unknown_are_false(self):
        """空与 unknown 必须判 False——这是 v0.3 默认决策：
        缺省时**不**默认中文，避免污染 L1 统计。"""
        for v in ("", "unknown", "UNKNOWN", "  ", "None"):
            self.assertFalse(_learner_l1_is_zh(v),
                             msg=f"未知值必须判 False: {v!r}")

    def test_independent_from_ui(self):
        """关键验证：UI 语言与 L1 独立。
        旧实现下 ui=en 会被当作 L1=en 处理（错误）；
        新实现下 _learner_l1_is_zh 看 learner_l1，与 UI 语言无关。
        此函数纯 L1 判定，UI 语言只是注释里的场景——函数本身不接收 UI。"""
        # 关键场景：UI=en 但 L1=zh（C4 多母语迁移的真实案例）
        self.assertTrue(_learner_l1_is_zh("zh"))     # L1=zh → True
        # L1 非中文 → 一律 False（与 UI 语言无关）
        self.assertFalse(_learner_l1_is_zh("ja"))    # L1=ja
        self.assertFalse(_learner_l1_is_zh("en"))    # L1=en
        self.assertFalse(_learner_l1_is_zh("ko"))    # L1=ko


class L1IsZhLegacyTest(unittest.TestCase):
    """_l1_is_zh（语义已重正为 UI 语言判定）保持兼容旧行为。"""

    def test_zh_variants_ui(self):
        for v in ("", "zh", "中文", "汉语", "chinese"):
            self.assertTrue(_l1_is_zh(v))

    def test_en_ui(self):
        self.assertFalse(_l1_is_zh("en"))
        self.assertFalse(_l1_is_zh("ja"))


class BuildPersonaBriefSignatureTest(unittest.TestCase):
    """build_persona_brief 新签名兼容旧调用。"""

    def _active_persona(self):
        # 任意非默认 persona，确保 build_persona_brief 输出非空
        return {"reply_style": "socratic"}

    def test_legacy_native_lang_still_works(self):
        """旧调用：build_persona_brief(persona, native_lang=en) 仍输出英文措辞。"""
        out = build_persona_brief(self._active_persona(), "en")
        self.assertIn("[Persona]", out)
        self.assertIn("guide with questions", out)  # 英文风格标签特征
        self.assertNotIn("启发引导", out)  # 中文标签不应出现

    def test_legacy_native_lang_zh(self):
        out = build_persona_brief(self._active_persona(), "zh")
        self.assertIn("启发引导", out)
        self.assertNotIn("Socratic", out)

    def test_new_learner_l1_kwarg_accepted(self):
        """新调用：build_persona_brief(persona, learner_l1="ja") 不抛错。"""
        # learner_l1 是 kwarg，目前 build_persona_brief 不依赖其值（语义重正在 _learner_l1_is_zh）
        # 但必须能接受新参数，否则 C4 阶段调用会失败
        out_zh = build_persona_brief(self._active_persona(), "zh", learner_l1="ja")
        out_en = build_persona_brief(self._active_persona(), "en", learner_l1="ja")
        # 措辞仍由 native_lang 决定（向后兼容；C4 阶段会把 learner_l1 也注入措辞）
        self.assertIn("启发引导", out_zh)
        self.assertIn("guide with questions", out_en)

    def test_learner_l1_does_not_change_legacy_wording(self):
        """v0.3 约束：learner_l1 字段不破坏旧措辞（C4 阶段再做 L1 个性化）。"""
        p = self._active_persona()
        out_no_l1 = build_persona_brief(p, "en")
        out_with_l1 = build_persona_brief(p, "en", learner_l1="ja")
        self.assertEqual(out_no_l1, out_with_l1,
                         msg="learner_l1 当前不应改变措辞")


class EmptyPersonaBackwardCompatTest(unittest.TestCase):
    """空 persona 仍回退空串（0.21 语言指令默认）。"""

    def test_empty_persona(self):
        self.assertEqual(build_persona_brief({}), "")
        self.assertEqual(build_persona_brief(None), "")

    def test_default_persona(self):
        self.assertEqual(build_persona_brief(dict(DEFAULT_PERSONA)), "")

    def test_default_persona_with_learner_l1(self):
        """全默认 persona + 新 learner_l1 字段仍回退空串。"""
        self.assertEqual(
            build_persona_brief(dict(DEFAULT_PERSONA), learner_l1="ja"), "")


if __name__ == "__main__":
    unittest.main()
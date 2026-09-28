# -*- coding: utf-8 -*-
"""A 面·朱笔批注定位：服务端契约 offset 可选键测试。

锁定：
① 识别主路径（LLM mock）confirmed/uncertain 均钉 offset = fragment 在原句字符起点；
② offset 语义 = str.find（0 基；找不到 -1；空 fragment -1）；
③ 降级路径 _rule_fallback 同样钉 offset；
④ ordered_error（v2 白名单）透传 offset，v1 不带（老契约零变化）；
⑤ 加法兼容：mock 无 fragment/offset → ordered_error offset=-1，不炸主链。
"""
import unittest
from engine.recognizer import Recognizer, locate_fragment
from engine.router import ordered_error


class FakeLLM:
    def __init__(self, raw=None):
        self.raw = raw if raw is not None else {"errors": []}

    def chat_json(self, system, user, **kw):
        return self.raw


class RaisingLLM:
    def chat_json(self, system, user, **kw):
        raise RuntimeError("recognition down")


def _recognizer(raw=None):
    return Recognizer(client=FakeLLM(raw))

# ---------------- ① locate_fragment 纯函数 ----------------
class LocateFragmentTest(unittest.TestCase):

    def test_finds_character_offset(self):
        self.assertEqual(locate_fragment("我想买苹果很多。", "苹果"), 3)
        self.assertEqual(locate_fragment("我想买苹果很多。", "很多"), 5)

    def test_multiple_takes_first(self):
        self.assertEqual(locate_fragment("好好好", "好"), 0)

    def test_missing_or_empty_returns_minus_one(self):
        self.assertEqual(locate_fragment("我想买苹果很多。", "不存在"), -1)
        self.assertEqual(locate_fragment("", "苹果"), -1)
        self.assertEqual(locate_fragment("我想买苹果很多。", ""), -1)
        self.assertEqual(locate_fragment("我想买苹果很多。", None), -1)
        self.assertEqual(locate_fragment(None, "苹果"), -1)

# ---------------- ② 识别主路径钉 offset ----------------
class RecognizeOffsetTest(unittest.TestCase):

    def test_confirmed_and_uncertain_carry_offset(self):
        raw = {"errors": [
            {"fragment": "苹果", "correction": "一些苹果", "type": "词汇",
             "type_confident": True, "confidence": 0.9},
            {"fragment": "很多", "correction": "很多苹果", "type": "语法",
             "type_confident": True, "confidence": 0.99},
            {"fragment": "几岁", "correction": "多大", "type": "语用",
             "type_confident": False, "confidence": 0.4},
        ]}
        text = "我想买苹果，买了很多，你几岁？"
        res = _recognizer(raw).recognize(text)
        for e in res["errors"] + res["uncertain"]:
            self.assertIn("offset", e, f"{e.get('fragment')} 应有 offset")
            self.assertEqual(e["offset"], text.find(e["fragment"]))
            self.assertGreaterEqual(e["offset"], 0)

# ---------------- ③ 降级路径钉 offset ----------------
class FallbackOffsetTest(unittest.TestCase):

    def test_rule_fallback_carries_offset(self):
        res = Recognizer(client=RaisingLLM()).recognize("苹果 一个。", level=3)
        for e in res["errors"] + res["uncertain"]:
            self.assertIn("offset", e)
            self.assertGreaterEqual(e["offset"], -1)

# ---------------- ④ ordered_error v1/v2 白名单 ----------------
class OrderedErrorOffsetTest(unittest.TestCase):

    def test_v2_passes_offset_through(self):
        err = {"fragment": "苹果", "offset": 3}
        out = ordered_error(err)
        self.assertEqual(out["offset"], 3)

    def test_v2_absent_offset_defaults_minus_one(self):
        # 识别层未产出 offset（老 mock / 外部构造）→ -1，加法兼容不炸
        out = ordered_error({"fragment": "苹果"})
        self.assertEqual(out["offset"], -1)
        out_none = ordered_error({"fragment": "苹果", "offset": None})
        self.assertEqual(out_none["offset"], -1)

    def test_v1_does_not_pick_offset(self):
        out = ordered_error({"fragment": "苹果", "offset": 3}, version=1)
        self.assertNotIn("offset", out)

    def test_all_v2_keys_listed(self):
        # 白名单不含内部扩展字段（offset 进白名单，raw/source 不进）
        out = ordered_error({"fragment": "苹果", "source": "x", "raw": {}})
        self.assertNotIn("source", out)
        self.assertNotIn("raw", out)
        self.assertIn("offset", out)


if __name__ == "__main__":
    unittest.main()
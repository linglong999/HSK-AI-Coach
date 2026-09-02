# ============================================================
# M5 多轮记忆 · 回归测试（tests/test_memory.py）
# 覆盖验收：append 落盘 / 裁剪 / 规范化 / 摘要 / 原子写 / 隔离 / 搜索
# ============================================================

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.memory.learner_memory import LearnerMemory, ALLOWED_ROLES


class TestLearnerMemory(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="hsk_mem_")
        self.mem = LearnerMemory(learner_id="test_user", root=self._tmp)

    def _path_exists(self):
        return os.path.exists(os.path.join(
            self._tmp, "memory_test_user.json"))

    def test_append_persists(self):
        self.mem.append("s1", "user", "你好")
        self.mem.append("s1", "assistant", "你好！")
        self.assertTrue(self._path_exists())
        hist = self.mem.get_history("s1")
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["role"], "user")
        self.assertEqual(hist[0]["content"], "你好")
        self.assertEqual(hist[1]["role"], "assistant")

    def test_append_invalid_role_rejected(self):
        with self.assertRaises(ValueError):
            self.mem.append("s1", "system", "不该能写")

    def test_window_truncation(self):
        for i in range(25):
            self.mem.append("s1", "user", f"msg{i}")
        hist = self.mem.get_history("s1", window=20)
        self.assertEqual(len(hist), 20)
        # 保留最近 20 条
        self.assertEqual(hist[0]["content"], "msg5")
        self.assertEqual(hist[-1]["content"], "msg24")

    def test_persist_window_caps_messages(self):
        # 超出 PERSIST_WINDOW 裁剪
        mem = LearnerMemory(learner_id="u", root=self._tmp)
        mem.PERSIST_WINDOW = 5  # 临时调小
        for i in range(10):
            mem.append("s1", "user", f"x{i}")
        self.assertEqual(len(mem.get_history("s1", window=999)), 5)
        self.assertEqual(mem.get_history("s1", window=999)[-1]["content"], "x9")

    def test_to_llm_history_filters(self):
        self.mem.append("s1", "user", "   ")
        self.mem.append("s1", "assistant", "有效内容")
        self.mem.append("s1", "user", "有内容")
        llm = self.mem.to_llm_history("s1")
        # 空消息被过滤，且只含 user/assistant
        self.assertEqual(len(llm), 2)
        self.assertNotIn("system", [m["role"] for m in llm])

    def test_summary_deterministic(self):
        self.mem.append("s1", "user", "问题A")
        self.mem.append("s1", "assistant", "回答B")
        s1 = self.mem.get_summary("s1")
        s2 = self.mem.get_summary("s1")
        self.assertEqual(s1, s2)  # 确定性
        self.assertIn("问题A", s1)
        self.assertIn("回答B", s1)

    def test_summary_truncates_long(self):
        long_text = "x" * 500
        self.mem.append("s1", "assistant", long_text)
        s = self.mem.get_summary("s1")
        # 内容被截断：行尾带省略号，且不含全部 500 个 x
        self.assertTrue(s.rstrip().endswith("…"))
        # 原始 500 个 x 被切到 max_chars(200)+省略号，未全量保留
        self.assertNotIn("x" * 201, s)

    def test_atomic_write_no_tmp_left(self):
        self.mem.append("s1", "user", "内容")
        leftover = [f for f in os.listdir(self._tmp) if f.endswith(".tmp")]
        self.assertEqual(leftover, [])
        self.assertTrue(self._path_exists())

    def test_learner_isolation(self):
        other = LearnerMemory(learner_id="another", root=self._tmp)
        self.mem.append("s1", "user", "只属于u")
        self.assertEqual(other.get_history("s1"), [])

    def test_search(self):
        self.mem.append("s1", "user", "我想学把字句")
        self.mem.append("s1", "assistant", "把字句用法讲解")
        hits = self.mem.search("把字句")
        self.assertEqual(len(hits), 2)
        self.assertIn("content", hits[0])
        self.assertNotIn("metadata", hits[0])  # 不暴露内部元数据

    def test_profile(self):
        self.mem.update_profile(native_lang="vi", hsk_level=3)
        p = self.mem.get_profile()
        self.assertEqual(p["native_lang"], "vi")
        self.assertEqual(p["hsk_level"], 3)

    def test_reload_from_disk(self):
        self.mem.append("s1", "user", "disk内容")
        mem2 = LearnerMemory(learner_id="test_user", root=self._tmp)
        mem2.reload()
        self.assertEqual(mem2.get_history("s1")[0]["content"], "disk内容")


if __name__ == "__main__":
    unittest.main()
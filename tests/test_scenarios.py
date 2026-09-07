# ============================================================
# tests/test_scenarios.py
# 0.22 方向2 · 场景对话技能端到端验收
# 覆盖：
#   A. 场景库数据完整性：load_scenarios / get_scene / list_scene_cards / kp_ids 对齐真实清单
#   B. build_scene_brief 双语注入段（en 壳 / zh 壳 / 非法场景 / 字符串 goals）
#   C. planner 单元注入：scene_brief → _build_system 拼 [Scene] 段（无场景则缺失）
#   D. HTTP：GET /api/scenarios 场景卡 + /api/dialog 带 scene_id 时注入 scene_brief
#   E. dialogue 生成：_validate_dialogue 最小校验 + generate_unit ok/degraded + prompt 契约
# 对齐铁律：不逐句纠错（brief 含约束）、scene_id 缺失退化自由对话（不阻断）。
# 运行: python -m unittest tests.test_scenarios -v
# ============================================================

import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.graph.error_graph import ErrorGraph
from engine.generation.generator import GenerationEngine
from engine.generation.authoring import build_dialogue_prompt
from engine.serve import make_handler
from planner.loop import Planner
from skills import build_registry
from skills.identify_errors import IdentifyErrorsSkill

# 方向2 被测模块
from engine import scenarios as S
from engine.scenarios import (
    build_scene_brief, get_scene, list_scene_cards, load_scenarios, resolve_kp_names,
)
from engine.generation.generator import GenerationEngine as _GE


# ---------------- 通用替身 ----------------

class FakeRecognizer:
    """固定返回一个已确认偏误（免 LLM、确定性，识别链路不断）。"""

    def recognize(self, text, level=3, native_lang=""):
        return {
            "errors": [{
                "fragment": "苹果很多", "correction": "很多苹果",
                "type": "语法-语序", "type_confident": True, "confidence": 0.9,
                "knowledge_point_id": "kp-order-many", "uncertain": False,
            }],
            "uncertain": [], "degraded": [],
        }


class FakeRouter:
    learner_id = "scene_user"

    def __init__(self):
        self.graph = ErrorGraph("scene_test")
        self.recognizer = FakeRecognizer()
        self.verifier = None
        self.explainer = None


class RecordingLLM:
    """记录每次 system 首条消息；按序返回 text 响应（默认直答，不触发技能）。"""

    def __init__(self):
        self.systems = []

    def __call__(self, messages):
        if messages:
            self.systems.append(messages[0].get("content", ""))
        return '[{"type":"text","content":"好的。"}]'


class MockClient:
    """可脚本化 mock：chat_json_strict 按序弹出 dict。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_json_strict(self, system, user, temperature=0.2, retries=1):
        self.calls.append(user)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _start_server(router, dialog_llm=None):
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "index.html"), "w", encoding="utf-8") as f:
        f.write("<html>scene</html>")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", port),
        make_handler(router, tmp, dialog_llm=dialog_llm, memory_root=tmp))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def _get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw.decode())
    except Exception:
        return r.status, None


def _post(port, path, body_bytes):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", path, body_bytes, {"Content-Type": "application/json"})
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw.decode())
    except Exception:
        return r.status, None


# ==================== A. 场景库数据完整性 ====================

class ScenarioDataTest(unittest.TestCase):

    def test_load_returns_5_scenes_unique_ids(self):
        scenes = load_scenarios()
        self.assertEqual(len(scenes), 5)
        ids = [s["scene_id"] for s in scenes]
        self.assertEqual(len(ids), len(set(ids)))
        for s in scenes:
            self.assertIn("title", s)
            self.assertIn("goals", s)
            self.assertIn("roles", s)
            self.assertTrue(s.get("level"))

    def test_scene_kp_ids_align_with_knowledge_points(self):
        """D2.4 对齐：每个场景 kp_ids 必须能在真实知识点清单里解析到名字。"""
        scenes = load_scenarios()
        self.assertTrue(scenes)
        for s in scenes:
            kp_ids = s.get("kp_ids") or []
            self.assertTrue(kp_ids, f"场景 {s['scene_id']} 无 kp_ids")
            names = resolve_kp_names(kp_ids)
            # 名称回退自身 == 清单里找不到 → 断言失败
            for kp_id in kp_ids:
                self.assertNotEqual(names.get(kp_id), kp_id,
                                    f"kp_id 未对齐真实清单: {kp_id}")

    def test_get_scene_found_and_missing(self):
        self.assertIsNotNone(get_scene("restaurant"))
        self.assertEqual(get_scene("restaurant")["title"], "At a restaurant")
        self.assertIsNone(get_scene("no-such-scene"))
        self.assertIsNone(get_scene(""))      # 空 → None，serve 据此忽略

    def test_list_scene_cards_subset_no_roles(self):
        cards = list_scene_cards()
        self.assertEqual(len(cards), 5)
        for c in cards:
            self.assertNotIn("roles", c)           # 卡片不暴露 roles 大段
            self.assertIsInstance(c["kp_count"], int)
            self.assertGreater(c["kp_count"], 0)
            self.assertTrue(c["scene_id"])
            self.assertTrue(c["title"])
            self.assertTrue(c["title_zh"])
            self.assertIsInstance(c["level"], list)

    def test_corrupt_or_missing_file_returns_empty(self):
        # 文件夹路径 → 读取异常 → 空库，不阻断
        self.assertEqual(load_scenarios(path=os.path.dirname(__file__)), [])
        self.assertIsNone(get_scene("x", path=os.path.dirname(__file__)))


# ==================== B. build_scene_brief 双语 ====================

class SceneBriefTest(unittest.TestCase):

    def _scene(self):
        return get_scene("restaurant")

    def test_brief_en_hierarchy(self):
        brief = build_scene_brief(self._scene(), native_lang="")
        self.assertIn("[Scene]", brief)
        self.assertIn("At a restaurant", brief)
        self.assertIn("customer", brief)            # 角色英壳
        self.assertIn("recast", brief)
        self.assertIn("Recast feedback", brief)
        self.assertIn("Skills to practice", brief)

    def test_brief_zh_hierarchy(self):
        brief = build_scene_brief(self._scene(), native_lang="zh")
        self.assertIn("[Scene]", brief)
        self.assertIn("餐厅点餐", brief)
        self.assertIn("你是顾客", brief)
        self.assertIn("自然重述", brief)             # P0.19 ⑨：复用 intervention 单一常量
        self.assertIn("recast 反馈", brief)
        self.assertIn("本场景练习知识点", brief)

    def test_brief_invalid_scene_empty(self):
        self.assertEqual(build_scene_brief({}, "zh"), "")
        self.assertEqual(build_scene_brief(None, ""), "")
        self.assertEqual(build_scene_brief({"no_id": 1}, ""), "")

    def test_brief_string_goals_normalized(self):
        s = get_scene("shopping")
        s = dict(s, goals="挑东西")
        self.assertIn("目标：挑东西", build_scene_brief(s, "zh"))


# ==================== C. planner 单元注入 ====================

class PlannerSceneInjectionTest(unittest.TestCase):

    def test_planner_injects_scene_brief_into_system(self):
        reg = build_registry(graph=ErrorGraph("scene_plan"),
                             recognizer=FakeRecognizer())
        llm = RecordingLLM()
        p = Planner(reg, llm_call=llm)
        r = p.run("你好", scene_brief="[Scene] 你走进一家餐厅。你是顾客，我是服务员。")
        self.assertFalse(r["fallback"])
        self.assertTrue(llm.systems)
        self.assertIn("[Scene]", llm.systems[0])
        self.assertIn("服务员", llm.systems[0])

    def test_planner_without_scene_no_scene_segment(self):
        reg = build_registry(graph=ErrorGraph("scene_plan2"),
                             recognizer=FakeRecognizer())
        llm = RecordingLLM()
        p = Planner(reg, llm_call=llm)
        p.run("你好")
        self.assertTrue(llm.systems)
        self.assertNotIn("[Scene]", llm.systems[0])


# ==================== D. HTTP：GET /api/scenarios + dialog 注入 ====================

class ScenariosHttpTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.router = FakeRouter()
        cls.llm = RecordingLLM()
        cls.httpd, cls.port = _start_server(cls.router, dialog_llm=cls.llm)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        p = os.path.join(_PROJECT_ROOT, "data", "graph_scene_test.json")
        if os.path.exists(p):
            os.remove(p)

    def test_api_scenarios_returns_scene_cards(self):
        st, out = _get(self.port, "/api/scenarios")
        self.assertEqual(st, 200)
        self.assertEqual(out["count"], 5)
        self.assertEqual(len(out["scenes"]), 5)
        card = next(c for c in out["scenes"] if c["scene_id"] == "restaurant")
        self.assertEqual(card["title"], "At a restaurant")
        self.assertNotIn("roles", card)

    def test_dialog_with_scene_id_injects_scene(self):
        del self.llm.systems[:]
        st, out = _post(
            self.port, "/api/dialog",
            json.dumps({"text": "你好", "scene_id": "restaurant",
                        "native_lang": "zh"}).encode())
        self.assertEqual(st, 200)
        self.assertTrue(any("[Scene]" in sysm and "服务员" in sysm
                            for sysm in self.llm.systems))

    def test_dialog_without_scene_no_scene_segment(self):
        del self.llm.systems[:]
        st, out = _post(self.port, "/api/dialog",
                        json.dumps({"text": "你好"}).encode())
        self.assertEqual(st, 200)
        self.assertTrue(self.llm.systems)
        self.assertFalse(any("[Scene]" in sysm for sysm in self.llm.systems))

    def test_dialog_unknown_scene_degrades_free_dialog(self):
        """未知 scene_id → 忽略场景，自由对话不阻断。"""
        del self.llm.systems[:]
        st, out = _post(self.port, "/api/dialog",
                        json.dumps({"text": "你好", "scene_id": "no-such"}).encode())
        self.assertEqual(st, 200)
        self.assertFalse(any("[Scene]" in sysm for sysm in self.llm.systems))


# ==================== E. dialogue 生成 ====================

def _valid_dialogue():
    return {
        "scene_id": "restaurant", "type": "dialogue",
        "title": "Ordering at a restaurant", "title_zh": "餐厅点餐",
        "level": [1, 2],
        "kp_ids": ["kp-liangci", "kp-nengyuan-dongci"],
        "seed": "你走进一家中国餐厅，想点一道菜。",
        "turns": ["向服务员打招呼", "问有什么招牌菜", "说要点的菜", "问价格"],
        "expressions": ["我要一个...", "请问...", "多少钱？"],
    }


class DialogueGenerationTest(unittest.TestCase):

    def test_validate_dialogue_valid(self):
        engine = _GE()
        self.assertEqual(engine._validate_dialogue(_valid_dialogue()), [])

    def test_validate_dialogue_missing_field(self):
        bad = _valid_dialogue()
        bad.pop("expressions")
        diags = _GE()._validate_dialogue(bad)
        # 缺失字段名进 evidence（message 是通用说明）
        self.assertTrue(any("expressions" in (d.evidence or "") for d in diags))

    def test_validate_dialogue_wrong_type(self):
        bad = _valid_dialogue()
        bad["type"] = "explain"
        self.assertTrue(_GE()._validate_dialogue(bad))

    def test_validate_dialogue_too_few_turns(self):
        bad = _valid_dialogue()
        bad["turns"] = ["只有一条"]
        self.assertTrue(_GE()._validate_dialogue(bad))

    def test_build_dialogue_prompt_includes_contract(self):
        prompt = build_dialogue_prompt(scene_id="restaurant", level=[1, 2],
                                       kp_ids=["kp-liangci"],
                                       seed="你走进餐厅。",
                                       language_directive="Chinese")
        self.assertIn('"scene_id"', prompt)
        self.assertIn("restaurant", prompt)
        self.assertIn('"type": "dialogue"', prompt)
        self.assertIn("kp-liangci", prompt)
        self.assertIn("Chinese", prompt)

    def test_generate_dialogue_ok(self):
        engine = _GE(client=MockClient([_valid_dialogue()]))
        out = engine.generate_unit("dialogue", {
            "scene_id": "restaurant", "level": [1, 2],
            "kp_ids": ["kp-liangci"], "seed": "你走进餐厅。",
        })
        self.assertTrue(out["ok"])
        self.assertEqual(out["unit_type"], "dialogue")
        self.assertEqual(out["unit"]["type"], "dialogue")
        self.assertEqual(out["unit"]["scene_id"], "restaurant")

    def test_generate_dialogue_ok_now_writeback(self):
        """dialogue 只产场景 brief，绝不写图谱。"""
        engine = _GE(client=MockClient([_valid_dialogue()]),
                     writeback=None, graph=ErrorGraph("scene_dg_gr"))
        out = engine.generate_unit("dialogue", {"scene_id": "restaurant"},
                                   write_back=True)
        self.assertTrue(out["ok"])
        self.assertNotIn("writeback", out)

    def test_generate_dialogue_degraded_on_bad_output(self):
        bad = _valid_dialogue()
        bad["turns"] = ["不足"]
        engine = _GE(client=MockClient([bad, bad]))
        out = engine.generate_unit("dialogue", {"scene_id": "restaurant"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "degraded")
        self.assertTrue(any("turns" in d.get("evidence", "") for d in out["diagnostics"]))

    def test_generate_dialogue_skill_enum_accepted(self):
        from skills.generate_unit import GenerateUnitSkill
        skill = GenerateUnitSkill(engine=_GE(client=MockClient([_valid_dialogue()])))
        out = skill.run({"unit_type": "dialogue",
                         "context": {"scene_id": "restaurant", "seed": "开场"}})
        self.assertTrue(out["ok"])
        self.assertEqual(out["unit_type"], "dialogue")

    def test_generate_dialogue_skill_unknown_type_degraded(self):
        from skills.generate_unit import GenerateUnitSkill
        skill = GenerateUnitSkill(engine=_GE())
        out = skill.run({"unit_type": "story"})
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "degraded")


if __name__ == "__main__":
    unittest.main(verbosity=2)
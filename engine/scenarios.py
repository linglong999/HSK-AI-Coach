# ============================================================
# engine/scenarios.py
# 0.22 方向2 · 预置场景库（D2.1-D2.4）
#   - load_scenarios / get_scene：读 datasets/scenarios_en.json（分发资产）
#   - resolve_kp_names：kp_ids → 可读知识点名（读 knowledge_points_v1_4.json）
#   - build_scene_brief：场景 → 对话主链注入的 [Scene] 段（双语，教学层跟随 native_lang）
#   - list_scene_cards：前端场景卡列表（只含卡片渲染所需字段）
# 纯确定性、零 LLM；场景缺失/损坏 → 空库（不阻断对话）。
# ============================================================

import json
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS_PATH = os.path.join(_PROJECT_ROOT, "datasets", "scenarios_en.json")
KNOWLEDGE_POINTS_PATH = os.path.join(_PROJECT_ROOT, "datasets", "knowledge_points_v1_4.json")

# 场景默认为"开场的产出句都要被识别喂图谱/迁移带"，但不逐句纠错——这是教学层指令。
# 具体纠错介入时机由 0.22 方向3 的确定性介入函数接管；场景短注入只约束"先开口、非必要不打断"。

_scenarios_cache = None          # (path, list[dict])
_kp_names_cache = None           # (path, {kp_id: name})


def _l1_is_zh(native_lang: str) -> bool:
    v = str(native_lang or "").strip().lower()
    zh = {"zh", "中文", "汉语", "chinese", "汉语官话"}
    return v in zh


def load_scenarios(path: str = SCENARIOS_PATH) -> list:
    """加载预置场景库（进程内缓存）。文件缺失/损坏 → 空列表（不阻断）。"""
    global _scenarios_cache
    if _scenarios_cache is not None and _scenarios_cache[0] == path:
        return _scenarios_cache[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        scenes = data.get("scenes", []) if isinstance(data, dict) else []
    except Exception:
        scenes = []
    scenes = [s for s in scenes if isinstance(s, dict) and s.get("scene_id")]
    _scenarios_cache = (path, scenes)
    return scenes


def get_scene(scene_id: str, path: str = SCENARIOS_PATH):
    """按 scene_id 取场景；未命中 → None（serve 层据此忽略 scene_id，不阻断）。"""
    if not scene_id:
        return None
    for s in load_scenarios(path):
        if s.get("scene_id") == scene_id:
            return s
    return None


def resolve_kp_names(kp_ids, path: str = KNOWLEDGE_POINTS_PATH) -> dict:
    """kp_ids → {kp_id: 中文知识点名}。清单缺失时名称回退为 kp_id（仍可注入）。"""
    global _kp_names_cache
    if _kp_names_cache is None or _kp_names_cache[0] != path:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            kps = data.get("knowledge_points", {}) if isinstance(data, dict) else {}
        except Exception:
            kps = {}
        _kp_names_cache = (path, {
            k: str((kp or {}).get("knowledge_point") or k)
            for k, kp in kps.items()
        })
    names = _kp_names_cache[1]
    return {k: names.get(k, k) for k in (kp_ids or [])}


def build_scene_brief(scene: dict, native_lang: str = "") -> str:
    """把场景编译成注入对话主链的 [Scene] 段（双语；教学层跟随 native_lang）。
    关键约束：学习者先开口；不逐句纠错（纠错介入交给方向3，此地仅教学层约束）。
    返回空串 = 无有效场景。"""
    if not isinstance(scene, dict) or not scene.get("scene_id"):
        return ""
    is_zh = _l1_is_zh(native_lang)
    roles = scene.get("roles") or {}
    goals = scene.get("goals") or []
    if isinstance(goals, str):
        goals = [goals]
    kp_names = resolve_kp_names(scene.get("kp_ids") or [])
    kp_line = "、".join(kp_names.values()) if kp_names else ""

    if is_zh:
        return (
            f"[Scene] 场景「{scene.get('title_zh') or scene.get('title')}」。"
            f"你是{roles.get('learner') or '学习者'}，我是{roles.get('agent') or '对话方'}。\n"
            f"目标：{'、'.join(goals)}。\n"
            + (f"本场景练习知识点：{kp_line}。\n" if kp_line else "")
            + "请先让我开口；不要逐句纠错，只有我卡壳、说不清或主动求助时才提示。"
        )
    skill_line = "、".join(kp_names.values()) if kp_names else ""
    # 英文壳用 roles_en（教学层英文）；缺省回退 roles 中文名
    roles_en = scene.get("roles_en") or {}
    ltext = roles_en.get("learner") or roles.get("learner") or "learner"
    atext = roles_en.get("agent") or roles.get("agent") or "partner"
    return (
        f"[Scene] You are in \"{scene.get('title')}\": "
        f"you play the {ltext}, I play the {atext}.\n"
        f"Goal: {' / '.join(goals)}.\n"
        + (f"Skills to practice: {skill_line}.\n" if skill_line else "")
        + "Let me speak first; do NOT correct sentence-by-sentence. "
          "Only step in when I am stuck, unclear, or ask for help."
    )


def list_scene_cards(path: str = SCENARIOS_PATH) -> list:
    """前端场景卡列表：只含卡片渲染字段（title/level/kp_count/goals/seed），
    不含 roles 大段——GET /api/scenarios 响应仅此子集。"""
    cards = []
    for s in load_scenarios(path):
        goals = s.get("goals") or []
        if isinstance(goals, str):
            goals = [goals]
        cards.append({
            "scene_id": s.get("scene_id"),
            "title": s.get("title"),
            "title_zh": s.get("title_zh") or s.get("title"),
            "level": s.get("level") or [],
            "kp_count": len(s.get("kp_ids") or []),
            "goals": goals,
            "seed": s.get("seed", ""),
        })
    return cards
# -*- coding: utf-8 -*-
# engine/meta_judgment.py —— B3 元认知判定（自评矩阵 + 自评时机，纯确定性）
# 对齐实施计划-v3 B3：判定矩阵 6 格路由偏置 + 自评时机两高危·每任务≤1。
# 数据契约：SELF_LEVELS 与 graph/model.Node.meta_confidence 值域一致；
# MATRIX 输出的是"路由偏置/调度权重微调"，不判对错——对错由 B1 诊断器/
# 识别 confirmed 确定性主判，自评只作旁证（B3-G1 注释：route_bias 永远不盖过
# B1 确定性判据）。

SELF_LEVELS = ("certain", "half", "uncertain")   # 确定/半确定/不确定（§5.2 ②三档）

# 判定矩阵 6 格：{(自评档, 产出)} → 路由偏置结构（§5.2 ⑤表逐格落码）
# outcome 取值："correct" | "wrong"（由调用方主判后传入，本模块不判）。
# "half"=校准 gap 中间态（B3-A 拍板：非第三习得状态），half+wrong=unlearned 核心信号。
MATRIX = {
    ("certain", "correct"):   {"route": "advance",       "scheduling": "long_interval", "note": "真懂·attempt_ok 旁证"},
    ("certain", "wrong"):     {"route": "light_mark",    "scheduling": "normal",         "note": "能力错觉·轻标1句+正常纠·错进图谱计数"},
    ("half", "correct"):      {"route": "review_boost",  "scheduling": "insert_deeper",  "note": "会但没把握·复习插队加深"},
    ("half", "wrong"):        {"route": "teach_light_model", "scheduling": "insert",     "note": "unlearned 核心信号·讲解+轻示范+插队"},
    ("uncertain", "correct"): {"route": "encourage",     "scheduling": "front_load",     "note": "保守低置信·鼓励+复习前置"},
    ("uncertain", "wrong"):   {"route": "silent_or_model", "scheduling": "front_load",   "note": "弱 avoided/unlearned·不逼·静默或轻示范+前置"},
}


def route_bias(self_level: str, outcome: str) -> dict:
    """产出→(对/错)·自评档 → 6 格路由偏置。
    纯函数：不判对错，只查表返回偏置结构 + 注记；非法档回落空偏置（不炸）。"""
    return dict(MATRIX.get((self_level, outcome), {"route": "", "scheduling": "", "note": ""}))


def should_ask_meta(target_kps_hit: list, outcome_has_error: bool,
                    already_asked_this_task: bool) -> tuple:
    """自评时机（§5.2 ③两高危·每任务≤1）：
    ①目标构式刚被正确产出一次后；②识别到疑似误用后。
    纯函数：given 本轮事件+目标构式命中+已问标记 → (ask?, anchor_kp)。
    防疲态：already_asked_this_task=True → 一律 False（每任务 ≤1）。
    anchor_kp 取第一个命中的目标构式 kp（标准 id，供 Node.meta_anchor）。"""
    if already_asked_this_task:
        return False, ""
    if not target_kps_hit:
        # 无目标构式命中时：疑似误用仍可问，锚点退空（由调用方回填或免锚）
        return (bool(outcome_has_error), "")
    anchor = target_kps_hit[0]
    return (True, anchor)
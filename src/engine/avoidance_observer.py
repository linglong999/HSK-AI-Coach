# -*- coding: utf-8 -*-
# engine/avoidance_observer.py —— B3 回避观测（该用未用·四态判定，纯确定性）
# 对齐实施计划-v3 B3：判定证据链 = 2 自动（语境必要=任务偏置 / 能力历史=查图谱）
#                        + 1 观察（替代表达=LLM 记录，人工抽查用，不进自动判定）。
# 处置=静默记录：avoided→"该复习"依据（喂 B4 调度权重）、unlearned→"该教"依据
# （喂 B5 排序层）；绝不进 intervention/directive（总纲 §2 拍板）。
# 无 focused 任务时（task_targets 空）→ 观测整体不激活，零行为退化态（B3 拍板3）。

# 图谱"该用未用"独立类型四态（区别于观测信号，见 G3 注释；映射关系有测试锁定）
GRAPH_STATES = ("avoided", "unlearned", "learned", "undetermined")

# 观测信号五态（总纲 §2 四级 + 保守态）：attempt_ok/attempt_err/avoided/
# unlearned/undetermined。与 GRAPH_STATES 两组并存、职责不同，勿混用。
OBSERVED_SIGNALS = ("attempt_ok", "attempt_err", "avoided", "unlearned", "undetermined")


def observe_avoidance(task_targets, learner_output, identify_result, graph_nodes):
    """本任务目标构式回避观测 → 逐 kp 判定 + 全量 trace（纯函数、零 LLM）。
    task_targets: 目标构式 kp 列表（focused 任务偏置即语境必要证据）；空→不激活。
    learner_output: 学习者产出原文（观察字段透传，供替代表达人工抽查）。
    identify_result: 识别结果 dict（errors/uncertain 两列表，取 kp_id 交集判定误用）。
    graph_nodes: {kp_id: Node} 图谱快照（查 positive_count 能力历史）。
    返回 {"per_kp": {kp_id: {state, positive_count, replacement, note}}, "trace": {...}}。
    判定细则（总纲 §2 锁定）：
      - 用了且对（该 kp 无错误报 + 产出含构式特征）→ attempt_ok：走正向链，
        图谱态可迁 learned（阈值归 B4 调度，本模块只产迁移通道）
      - 用了但错（identify 报该 kp 错误）→ attempt_err：走偏误识别既有链，态迁 unlearned
      - 没用 + positive_count>0 → avoided（该复习路径）
      - 没用 + positive_count==0 → unlearned（该教路径）
      - 偏置弱/无法判定机会 → undetermined（B3-A 拍板：凡判不准一律保守态）"""
    if not task_targets:
        # 无 focused 任务 → 观测整体不激活（零行为退化态）
        return {"per_kp": {}, "trace": {"active": False, "learner_output": learner_output}}

    err_kps = {e.get("knowledge_point_id") or e.get("kp_candidate") or ""
               for e in (identify_result or {}).get("errors", [])}
    err_kps |= {e.get("knowledge_point_id") or e.get("kp_candidate") or ""
                for e in (identify_result or {}).get("uncertain", [])}
    err_kps.discard("")

    per_kp = {}
    for kp in task_targets:
        node = (graph_nodes or {}).get(kp)
        pos = int(getattr(node, "positive_count", 0)) if node else 0
        replacement = ""  # 观察字段：LLM 记录绕行说法（人工抽查用），自动判定不读它
        if kp in err_kps:
            # 用了但错 → attempt_err，态迁 unlearned
            per_kp[kp] = {"state": "unlearned" if pos == 0 else "unlearned",
                          "signal": "attempt_err", "positive_count": pos,
                          "replacement": replacement,
                          "note": "用到但报错·attempt_err·偏误识别既有链"}
        elif _contains_construction_marker(learner_output):
            # 用了且对走正向链（attempt_ok）；图谱态由 B4 attempt_ok 累积迁移 learned
            per_kp[kp] = {"state": "attempt_ok", "signal": "attempt_ok",
                          "positive_count": pos, "replacement": replacement,
                          "note": "用了且对·attempt_ok·正向链·learned 迁移阈值归 B4"}
        else:
            # 没用 → 凭能力历史分路：有历史能力→该复习(avoided)；无→该教(unlearned)
            if pos > 0:
                per_kp[kp] = {"state": "avoided", "signal": "avoided",
                              "positive_count": pos, "replacement": replacement,
                              "note": "没用+有历史能力·回避·该复习路径"}
            else:
                per_kp[kp] = {"state": "unlearned", "signal": "unlearned",
                              "positive_count": pos, "replacement": replacement,
                              "note": "没用+无能力证据·unlearned·该教路径"}
    return {"per_kp": per_kp,
            "trace": {"active": True, "targets": list(task_targets),
                      "err_kps": sorted(err_kps),
                      "learner_output": learner_output}}


def _contains_construction_marker(output: str) -> bool:
    """极轻量构式特征探测（非分词，仅形态信号，B3 骨架期不引分词）：
    只认具构式形态价值的"把"（把字句）与"得"（状态/程度补语）。
    体标记"了/着/过"不单列为构式特征——它们常见于非目标构式的普通陈述句，
    若纳入会把"用得少+绕行"误判成"用了"。精判定由 attempt_ok/attempt_err
    走既有链（B1 诊断器/识别 confirmed）承担。"""
    if not output:
        return False
    return any(m in output for m in ("把", "得"))
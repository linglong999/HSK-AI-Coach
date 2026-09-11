# ============================================================
# engine/metrics.py
# P0.18 · 效果度量与数据闭环（蓝本 V1 P0.18）
# 纯标准库，零第三方。输入 data/ 下 ledger_/graph_/memory_/feedback_<learner>.json，
# 按 learner_id 关联（以 ledger_*.json 存在为 learner 锚点，避免误匹配无 ledger 的图变体）。
# 4 指标，每个返回 {value, n, window, caveats}：
#   value   指标值；无数据/数据不足时返回 None（如实报缺，不编数）
#   n       样本量（据以判断可信度）
#   window  口径的时间窗口
#   caveats 口径与局限（与 reports/metrics_caliber.md 一致）
# ============================================================

import glob
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

# 通过判定：graph 节点 positive 痕迹
_POSITIVE_KINDS = ("concept_confirmed",)


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "_", name)


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------- 学习者发现与文件装配 ----------------
def iter_learners(root: str = "data") -> List[str]:
    """从 ledger_*.json 发现 learner（组件锚点）。返回按名的 learner_id 列表。"""
    seen: List[str] = []
    for fp in sorted(glob.glob(os.path.join(root, "ledger_*.json"))):
        lid = fp[len(root) + 1: -len("ledger_") - 1] if False else ""
        name = os.path.basename(fp)[len("ledger_"):-len(".json")]
        if name and name not in seen:
            seen.append(name)
    return sorted(seen)


def _file(root: str, kind: str, learner: str) -> str:
    return os.path.join(root, f"{kind}_{_safe(learner)}.json")


def _load_all(root: str, learner: str) -> Dict[str, Any]:
    led = _read_json(_file(root, "ledger", learner))
    graph = _read_json(_file(root, "graph", learner))
    mem = _read_json(_file(root, "memory", learner))
    fb = _read_json(_file(root, "feedback", learner))
    return {"ledger": led, "graph": graph, "memory": mem, "feedback": fb}


# ---------------- 内部口径小工具 ----------------
def _error_events(ledger: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [e for e in ledger.get("events", [])
            if e.get("kind") in ("observation_error", "repeated_error")]


def _passed_kps(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
    """返回已通过（有 positive 痕迹）的节点名单：[{id, t}]，t=通过时间戳。"""
    out: List[Dict[str, Any]] = []
    nodes = graph.get("nodes", {})
    if not isinstance(nodes, dict):
        return out
    for nid, node in nodes.items():
        if not isinstance(node, dict):
            continue
        t = node.get("last_positive_at")
        pc = node.get("positive_count", 0)
        if t or pc:
            out.append({"id": nid, "t": t or 0})
    return out


def _density(events: List[Dict[str, Any]], t0: float, t1: float) -> float:
    """[t0, t1) 窗内错误事件数 / 窗内天数（>=1，防除零）。"""
    days = max(1.0, (t1 - t0) / 86400.0)
    cnt = sum(1 for e in events
              if e.get("ts") is not None and t0 <= e["ts"] < t1)
    return cnt / days


# ---------------- 指标 1：知识点通过率变化 ----------------
def pass_rate_delta(learner: str, root: str = "data",
                    window_days: float = 14.0) -> Dict[str, Any]:
    """同一 kp 首次通过前后错误事件密度的对比。
    口径：对每个已通过 kp，取首次通过时刻 T；对比 [T-W,T) 与 [T,T+W) 的错误事件
    密度（错误数/天）；delta = 通过后 - 通过前（负=变得更少出错=进步）；
    聚合所有已通过 kp 取均值。通过前无任何错误事件 → 该 kp 单独标记 no_before。"""
    d = _load_all(root, learner)
    events = _error_events(d["ledger"])
    kps = _passed_kps(d["graph"])
    rows: List[Dict[str, Any]] = []
    no_before = 0
    agg_before = agg_after = 0.0
    for kp in kps:
        T = kp["t"] or 0
        if not T:
            continue
        kp_events = [e for e in events if e["kp_id"] == kp["id"]]
        before = _density(kp_events, T - window_days * 86400, T)
        after = _density(kp_events, T, T + window_days * 86400)
        if before == 0 and after == 0:
            no_before += 1
            continue
        rows.append({"kp": kp["id"], "before": round(before, 4),
                     "after": round(after, 4)})
        agg_before += before
        agg_after += after
    if not rows:
        return {"value": None, "n": 0, "window": f"±{window_days:.0f}d",
                "caveats": "该学习者尚无任何通过记录（positive_count=0 / 无 last_positive_at），指标 1 无法计算，如实报缺。"}
    return {
        "value": round((agg_after - agg_before) / len(rows), 4),
        "n": len(rows),
        "window": f"±{window_days:.0f}d 窗口 / 已通过 kp 计数",
        "caveats": ("「通过」以 graph 节点 last_positive_at/positive_count 为准；为通过前后错误事件密度差"
                    "（错误数/天），负=通过后更少出错。近似指标的横截面，非逐轮判定；"
                    "通过前无错误记录者已剔除（%d 个）。" % no_before),
        "detail": rows,
    }


# ---------------- 指标 2：达标交互数 ----------------
def interactions_to_pass(learner: str, root: str = "data") -> Dict[str, Any]:
    """kp 首次出错 → 首次通过 之间的交互轮数（以期间 ledger 事件数为代理）。
    口径：对每个已通过 kp，首次 error 事件 ts 到通过时刻 T 之间的事件条数（observation/
    repeated/concept_reviewed 均计为一次交互），即『这次知识点的纠错来回』；取各 kp 均值。
    通过"前无任何 error 事件 → 该 kp 无法测（首次即对）。"""
    d = _load_all(root, learner)
    ledger = d["ledger"]
    events = [e for e in ledger.get("events", [])
              if e.get("ts") is not None and e.get("kp_id")]
    kps = _passed_kps(d["graph"])
    rows: List[Dict[str, Any]] = []
    skipped = 0
    for kp in kps:
        T = kp["t"] or 0
        if not T:
            continue
        same = [e for e in events if e["kp_id"] == kp["id"]]
        first_err = min((e["ts"] for e in same
                         if e.get("kind") in ("observation_error", "repeated_error")),
                        default=None)
        if first_err is None or first_err >= T:
            skipped += 1
            continue
        rounds = sum(1 for e in same if first_err <= e["ts"] <= T)
        rows.append({"kp": kp["id"], "rounds": rounds})
    if not rows:
        return {"value": None, "n": 0, "window": "首错→首通过",
                "caveats": ("该学习者无『兼有出错与通过』的 kp（无通过记录；或通过但从未出错），"
                            "指标 2 无法计算，如实报缺。")}
    avg = sum(r["rounds"] for r in rows) / len(rows)
    return {
        "value": round(avg, 2),
        "n": len(rows),
        "window": "首错→首通过 事件轮数",
        "caveats": ("『交互轮数』以该 kp 在首错至首通过之间 ledger 事件条数为代理，非真实轮次；"
                    "首错晚于首通过者剔除（%d 个）。" % skipped),
        "detail": rows,
    }


# ---------------- 指标 3：续学信号 ----------------
def continue_learning_rate(learner: str, root: str = "data",
                           horizon_hours: float = 24.0) -> Dict[str, Any]:
    """当日会话结束后主动回来学习的比例。
    口径：记忆中的会话按 [created_at, updated_at] 视为一段学习活动；统计所有『当天有会话
    结束（updated_at）』的日期 D，其中在 D 结束后 horizon_hours 内又有新会话开始的日期
    记为「续学」；续学率 = 续学日期数 / 有效日期数。"""
    d = _load_all(root, learner)
    sessions = d["memory"].get("sessions", {})
    if not isinstance(sessions, dict):
        sessions = {}
    acts: List[float] = []
    for conv in sessions.values():
        if not isinstance(conv, dict):
            continue
        ca = conv.get("created_at")
        ua = conv.get("updated_at")
        if not ca:
            continue
        acts.append((float(ca), float(ua or ca)))
    acts.sort()
    # 结束日日期键 → 是否续学
    import datetime
    ends: Dict[str, bool] = {}
    for ca, ua in acts:
        dkey = datetime.date.fromtimestamp(ua).isoformat()
        ends.setdefault(dkey, False)
        for ca2, ua2 in acts:
            if ca2 > ua and (ca2 - ua) <= horizon_hours * 3600:
                ends[dkey] = True
                break
    total = len(ends)
    if total == 0:
        return {"value": None, "n": 0, "window": f"结束日基数 / {horizon_hours:.0f}h 续学窗",
                "caveats": "该学习者无任何带时间戳的会话记录，指标 3 无法计算，如实报缺。"}
    succ = sum(1 for v in ends.values() if v)
    return {
        "value": round(succ / total, 4),
        "n": total,
        "window": f"结束日基数 / {horizon_hours:.0f}h 续学窗",
        "caveats": ("以会话级 created_at/updated_at 估学习活动，非轮级粒度；"
                    "‘续学’= 某结束日后 %.0f 小时内又开始新会话。同日多次会话会计 1 个结束日。"
                    % horizon_hours),
    }


# ---------------- 指标 4：答疑满意度 ----------------
def satisfaction(learner: str, root: str = "data") -> Dict[str, Any]:
    """对话结束评分均值（评分入口自愿提交，1–5）。"""
    d = _load_all(root, learner)
    scores = d["feedback"].get("scores", [])
    values = [s.get("score") for s in scores
              if isinstance(s, dict) and isinstance(s.get("score"), (int, float))]
    if not values:
        return {"value": None, "n": 0, "window": "累计评分",
                "caveats": "尚无评分数据（评分入口提交后才累计），指标 4 如实报缺。"}
    return {
        "value": round(sum(values) / len(values), 2),
        "n": len(values),
        "window": "累计评分",
        "caveats": ("自愿提交偏差（不满意者可能不评）；样本少时波动大，看方向不追显著。"),
    }


# ---------------- 汇总与反馈记录 ----------------
def summary(learner: str, root: str = "data") -> str:
    """4 指标汇总为 markdown 摘要（复用 summarize.py 的简洁风格）。"""
    m1 = pass_rate_delta(learner, root)
    m2 = interactions_to_pass(learner, root)
    m3 = continue_learning_rate(learner, root)
    m4 = satisfaction(learner, root)
    lines = [f"## 效果度量（{learner}）"]
    labels = [
        ("通过率变化", m1, lambda v: f"{v:+.4f} 错/天"),
        ("达标交互数", m2, lambda v: f"{v:.1f} 轮"),
        ("续学率", m3, lambda v: f"{v*100:.0f}%"),
        ("满意度", m4, lambda v: f"{v:.2f}/5"),
    ]
    for name, m, fmt in labels:
        if m["value"] is None:
            lines.append(f"- {name}：_无数据（n=0）_ — {m['caveats']}")
        else:
            lines.append(f"- {name}：{fmt(m['value'])}（n={m['n']}）")
    return "\n".join(lines)


def all_metrics(root: str = "data") -> Dict[str, Any]:
    """聚合全部 learner 的 4 指标，供 /api/metrics。"""
    out: Dict[str, Any] = {}
    for learner in iter_learners(root):
        out[learner] = {
            "pass_rate_delta": pass_rate_delta(learner, root),
            "interactions_to_pass": interactions_to_pass(learner, root),
            "continue_learning_rate": continue_learning_rate(learner, root),
            "satisfaction": satisfaction(learner, root),
        }
    return {"learners": out, "generated_at": int(time.time())}


# ---------------- 反馈记录（评分入口 → data/feedback_<learner>.json） ----------------
def record_feedback(root: str, learner: str, score: float,
                    conversation_id: str = "", comment: str = "") -> bool:
    """追加一条评分到 feedback_<learner>.json（tmp+replace 原子写）。
    返回是否成功；score 越界/非数则拒绝且返回 False。"""
    if not learner:
        return False
    try:
        score = float(score)
    except (TypeError, ValueError):
        return False
    if not (1 <= score <= 5):
        return False
    path = _file(root, "feedback", learner)
    data = _read_json(path)
    data.setdefault("learner_id", learner)
    data.setdefault("scores", [])
    data["scores"].append({
        "score": score,
        "conversation_id": str(conversation_id or ""),
        "comment": str(comment or "")[:200],
        "ts": int(time.time()),
    })
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return True
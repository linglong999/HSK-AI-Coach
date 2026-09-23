# ============================================================
# D-6 · 八条可断言约束（确定性计数脚本 + 全走 pytest assert，LLM judge 不承担计数）
# B6-A 拍板：计数断言=确定性文本统计 + pytest，LLM 只管 D-4 语义质量、不碰计数。
# 计数来源口径：round 结果字段优先（deep_dive_count/restate_count/meta_ask_count/
#   unpack_beyond_words 顶层键）；字段缺失时该条**容忍跳过**（非违规）——B2/B3/B5
#   各批阶段性暴露后此文件为唯一汇聚点，无需改动即可随批次字段就位自动生效。
# ⑥架构断言走源码 token 级检查（"rg 级"），不烧 LLM、不进 LLM 计数。
# 返回违规清单 list[str]：空 = 全过。缺省容错：任何来源缺失 → 跳过（不误伤、不假过）。
# ============================================================
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.avoidance_observer import GRAPH_STATES, OBSERVED_SIGNALS  # noqa: E402

# 诊断器三值门槛（B0/B1 契约）：score≥0.85→acceptable / ≥0.60→edge / <0.60→error
_VERDICT_DOMAINS = (("acceptable", 0.85), ("edge", 0.60))
# ⑥ 诊断器不得引入的 LLM 客户端 token（"rg 级"精确近似，见模块 docstring）
_FORBIDDEN_LLM_TOKENS = ("openai", "deepseek", "qwen", "LLMClient",
                         "provider", "httpx", "requests", "urllib.request")

# round 顶层四计数键（append-only；批次就位后自然被读取）
ROUND_COUNTERS = ("deep_dive_count", "restate_count", "meta_ask_count",
                  "unpack_beyond_words")


def _verdict_domain(score: float) -> str:
    """三值切分域：按 score 上界判定档位（对齐 VERDICT_ACCEPTABLE/EDGE）。"""
    for verdict, low in _VERDICT_DOMAINS:
        if score >= low:
            return verdict
    return "error"


def _diagnostics_source() -> str:
    """返回 construction_diagnostics.py 源码文本；找不到返回 ""（⑥容错）。"""
    try:
        from engine import construction_diagnostics as cd
    except Exception:  # noqa: BLE001 缺源 → 跳过⑥
        return ""
    path = getattr(cd, "__file__", "")
    if not path or not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def assert_d6(round_result: dict, graph_snapshot: dict, lex: dict = None) -> list:
    """八条确定性断言。返回违规清单（空 = 全过）。
    round_result：dialog 单轮 round 结果（可含四计数键 + construction_diagnostics）。
    graph_snapshot：ErrorGraph.graph_snapshot() → {"nodes": {kp: to_dict()}}。
    lex：词表（签名兼容；④解包违规清单已在 round_result 内，无需 lex 再判）。
    任一来源缺失 → 该条跳过（缺省容错，B0-B5 字段缺省期独立可跑）。"""
    r = round_result or {}
    viol = []

    # ① deep_dive_count ≤ 1（按错误点口径，B2 已产）
    if "deep_dive_count" in r and r.get("deep_dive_count") is not None \
            and int(r["deep_dive_count"]) > 1:
        viol.append(f"d6-1: deep_dive_count={r['deep_dive_count']}>1（深攻超限）")
    # ② restate_count ≤ 1（同一深攻重述）
    if "restate_count" in r and r.get("restate_count") is not None \
            and int(r["restate_count"]) > 1:
        viol.append(f"d6-2: restate_count={r['restate_count']}>1（重述超限）")
    # ③ meta_ask_count ≤ 1（每任务元认知自评，B3 已产）
    if "meta_ask_count" in r and r.get("meta_ask_count") is not None \
            and int(r["meta_ask_count"]) > 1:
        viol.append(f"d6-3: meta_ask_count={r['meta_ask_count']}>1（自评超限）")
    # ④ unpack_beyond_words == []（解包无超纲，B5 unpack_guard）
    ubw = r.get("unpack_beyond_words")
    if ubw is not None:
        if isinstance(ubw, list) and ubw:
            viol.append(f"d6-4: 解包超纲词 {len(ubw)} 个（首词{ubw[0] if isinstance(ubw[0], dict) else ubw[0]}）")
        elif not isinstance(ubw, list):
            viol.append(f"d6-4: unpack_beyond_words 类型异常=非列表({type(ubw).__name__})")
    # ⑤ 诊断器三值门槛：construction_diagnostics.score 落 0.85/0.6 切分域
    diag = r.get("construction_diagnostics")
    if isinstance(diag, dict) and diag.get("score") is not None:
        score = float(diag["score"])
        verdict = diag.get("verdict")
        if _verdict_domain(score) != verdict:
            viol.append(f"d6-5: score={score}→{_verdict_domain(score)} ≠ verdict={verdict}")
    # ⑥ 诊断器不生成只校验：无 LLM import（源码 token 级检查）
    src = _diagnostics_source()
    if src:
        hit = next((t for t in _FORBIDDEN_LLM_TOKENS if t in src), None)
        if hit:
            viol.append(f"d6-6: construction_diagnostics.py 检出 LLM token「{hit}」")
    # ⑦ 回避独立类型：Node 上回避域字段与错误域字段分列，值 ⊆ 图谱四态
    nodes = dict((graph_snapshot or {}).get("nodes") or {})
    if nodes:
        for kp, nd in nodes.items():
            av = nd.get("avoidance_state")
            if av is not None and av not in GRAPH_STATES:
                viol.append(f"d6-7: {kp} avoidance_state={av} 不在图谱四态{GRAPH_STATES}")
    # ⑧ 回避四态分离：观测信号值域 ⊆ 枚举、图谱四态恰为四态
    if GRAPH_STATES != ("avoided", "unlearned", "learned", "undetermined"):
        viol.append(f"d6-8: GRAPH_STATES 期望四态，实为 {GRAPH_STATES}")
    if len(OBSERVED_SIGNALS) != 5:
        viol.append(f"d6-8: OBSERVED_SIGNALS 期望 5 值，实为 {OBSERVED_SIGNALS}")
    if nodes:
        for kp, nd in nodes.items():
            av = nd.get("avoidance_state")
            if av is not None and av not in GRAPH_STATES:
                continue  # ⑦已报；⑧只查枚举自身完整性
    return viol


if __name__ == "__main__":
    ok = assert_d6({}, {"nodes": {}}, {})
    print("D6 全过（缺省容错）" if not ok else "D6 违规:\n- " + "\n- ".join(ok))
    sys.exit(0 if not ok else 1)
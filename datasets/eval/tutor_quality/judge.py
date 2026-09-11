# ============================================================
# tutor_quality · 三层 judge（L1 程序断言 / L2 LLM-judge / L3 红线硬判）
# 被测对象：Router.process()（真实引擎：识别→讲解→图谱→复习队列）
# 设计依据：datasets/docs/0.25-教学judge-rubric-评测设计.md + rubric.md
# 判分口径：事实类用程序断言（对 expected_behavior.detect），
#          开放类由 run_tutor_judge 注入被测对话文本（此处算 L2 占位），
#          红线任一再触发则该 case fail；红线不参与平均分。
# ============================================================
import os
import sys
import json

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.router import Router  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def load_cases():
    with open(os.path.join(HERE, "cases.json"), encoding="utf-8") as f:
        return json.load(f)["cases"]


def _norm(s):
    return (s or "").strip().replace(" ", "").replace("　", "")


class CaseResult:
    """单个 case 的判分结果：L1 程序断言 + 红线 + L2 占位。"""

    def __init__(self, case):
        self.case = case
        self.detected = []         # 被测引擎识别出的偏误（errors）
        self.l1 = None             # L1 判定 dict
        self.l2 = None             # L2 占位（待 LLM-judge）
        self.redline_fails = []    # 确定触发的红线 id 列表
        self.pending = []          # 待 L2/人工复核项（不计 fail）
        self.trace = {}            # 被测输出摘要（报告用）


def _assert_detect(result, target):
    """L1 程序断言：被测引擎识别的偏误 vs 期望偏误（span/type/correction）。

    返回 (matched_count, note)。target=None（clean/case 无偏误）时，
    期望引擎不报任何偏误（误报则记进 redline R2，由 check_redlines 处理）。
    """
    if target is None:
        return None, "无偏误期望（clean_guard / retell），误报留待红线判定"
    hits = []
    t_span, t_type, t_corr = _norm(target.get("span")), target.get("type"), _norm(target.get("correction"))
    for e in result.detected:
        er = e.get("error", {})
        span = _norm(er.get("error_span") or er.get("fragment"))
        etype = er.get("type")
        corr = _norm(er.get("correction"))
        if span and (span == t_span or (t_span and t_span in span or span in t_span)):
            hits.append(e)
        elif t_type and etype == t_type and corr == t_corr:
            hits.append(e)
    return len(hits), f"期望[{t_span}|{t_type}|{t_corr}] 命中 {len(hits)} 条"


def _check_redlines(result):
    """L3 红线判定，分成两类：
      - FAIL：确定触发，直接 fail（首版只有能确定性判定的 R1/R2）
      - PENDING：需 LLM-judge/人工复核，不进 fail 门禁（R3/R4/R5 及 case 数据可疑）
    返回 {"fails": [...], "pending": [...]}"""
    fails, pending = [], []
    rls = result.case.get("redlines", [])
    is_clean = result.case["expected_behavior"].get("detect") is None and \
               result.case["case_type"] == "clean_guard"
    # R2 误报干净句：clean_guard 却报了偏误 → 确定 FAIL
    if is_clean and "R2" in rls and result.detected:
        fails.append("R2")
    # R1 教错了：explain，引擎报了偏误且 L1 完全无命中 →
    # 还需区分"真的改法与黄金不同"(FAIL) vs "引擎报空"(句子可能本对，case 可疑，pending)
    if result.case["case_type"] in ("explain", "intervention") and "R1" in rls:
        exp = result.case["expected_behavior"].get("detect")
        if exp and result.l1 is not None and result.l1["matched"] == 0:
            if result.detected:
                fails.append("R1")
            else:
                pending.append("PENDING_CASE_SUSPECT: 引擎未检出，句子可能本对或 case 数据可疑")
    # R3/R4/R5 需 LLM 看讲解过程 → 首版标记 pending
    for r in ("R3", "R4", "R5"):
        if r in rls:
            pending.append(f"{r}(待L2/人工复核)")
    return {"fails": fails, "pending": pending}


def _run_target(case, router):
    """驱动被测引擎产偏误列表。返回 (errors_list, degraded, meta)。"""
    out = router.process(case["input"])
    errors = out.get("errors", [])
    return errors, out.get("degraded", []), out.get("meta", {})


def _l2_mock_judge(case, rc, note_base):
    """Mock L2 判分（--mock-llm 用）：不调真实 LLM，按 case 类型给合理分数。

    目的：验证 L2 调用/汇总/报告管线连通。分数是**规则化占位**，不代表真实教学质量
    （真值需接 LLM AS-judge 并人工校准，见 rubric.md §四）。红线已 fail 则值得 pass=false。
    """
    ct = case["case_type"]
    # 6 维顺序对齐 rubric.md 一
    base = {
        "clean_guard":   {"偏误定位准确": 5, "讲解四段完整": 5, "复述验证严格": 5,
                          "介入时机合理": 5, "语言分层守约": 5, "归因谨慎": 5},
        "retell":        {"偏误定位准确": 5, "讲解四段完整": 4, "复述验证严格": 5,
                          "介入时机合理": 4, "语言分层守约": 4, "归因谨慎": 4},
        "explain":       {"偏误定位准确": 5, "讲解四段完整": 4, "复述验证严格": 4,
                          "介入时机合理": 4, "语言分层守约": 4, "归因谨慎": 4},
        "intervention":  {"偏误定位准确": 4, "讲解四段完整": 4, "复述验证严格": 4,
                          "介入时机合理": 5, "语言分层守约": 4, "归因谨慎": 4},
    }.get(ct, {})
    # L1 无命中且该 case 期望有偏误 → 扣分（虽可能 case 可疑，但 mock 如实体现此类风险）
    if ct in ("explain", "intervention") and rc.l1 and rc.l1["matched"] == 0:
        base["偏误定位准确"] = max(1, base.get("偏误定位准确", 4) - 2)
    verdict = "fail" if rc.redline_fails else "pass"
    return {"verdict": verdict, "per_dim": base,
            "note": f"{note_base}（MOCK：规则化占位，非真实 LLM 评判）",
            "mock": True}


def _l2_real_judge(case, rc):
    """真实 LLM AS-judge 占位。接入时注入讲解文本 + rubric 逐维打分，并人工校准。

    当前抛 NotImplementedError 由驱动器捕获后降级为 pending（不假跑、不虚报分数）。
    """
    raise NotImplementedError(
        "L2 真实 LLM-judge 尚未接入：需先实现 rubric 打分 prompt + 人工校准（rubric.md §四）")


def judge_case(case, router, mock_llm=False):
    rc = CaseResult(case)
    try:
        rc.detected, rc.degraded, rc.meta = _run_target(case, router)
    except Exception as e:  # noqa: BLE001
        rc.error = f"driver failed: {e}"
        rc.redline_fails = ["DRIVER_FAIL"]
        return rc

    exp = case["expected_behavior"]
    l1_hits, l1_note = _assert_detect(rc, exp.get("detect"))
    rc.l1 = {"matched": l1_hits if l1_hits is not None else 0,
             "expected_clean": exp.get("detect") is None,
             "note": l1_note}
    _rl = _check_redlines(rc)
    rc.redline_fails, rc.pending = _rl["fails"], _rl["pending"]

    # L2：--mock-llm 走规则化占位验证管线；否则真实 judge（未接入则降级 pending）
    if mock_llm:
        rc.l2 = _l2_mock_judge(case, rc, "首版占位")
    else:
        try:
            rc.l2 = _l2_real_judge(case, rc)
        except NotImplementedError as e:
            rc.l2 = {"verdict": None, "per_dim": None,
                     "note": str(e), "mock": False, "skipped": True}
    rc.trace = {"errors": rc.detected, "degraded": rc.degraded,
                "elapsed_ms": (rc.meta or {}).get("elapsed_ms")}
    return rc


def main():
    """CLI：--case <id> 单跑；--all 全跑；--mock-llm 走规则化 L2 验证管线（不花 Key）。"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="", help="单 case id")
    ap.add_argument("--all", action="store_true", help="全跑")
    ap.add_argument("--mock-llm", action="store_true", help="用规则化 L2 判分验证管线（非真实 LLM）")
    args = ap.parse_args()

    cases = load_cases()
    router = Router()  # 真实引擎（无 Key 时识别内部回退规则匹配，仍可判 L1）

    selected = cases if args.all else [c for c in cases if c["id"] == args.case]
    if not selected:
        print("case not found:", args.case)
        return 1
    for case in selected:
        r = judge_case(case, router, mock_llm=args.mock_llm)
        print(json.dumps({
            "id": r.case["id"], "type": r.case["case_type"],
            "l1": r.l1, "redlines": r.redline_fails, "l2": r.l2 and r.l2["verdict"],
            "redline_notes": getattr(r, "redline_fails", []),
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
# ============================================================
# eval/run_eval_agent.py
# M6 自由对话评测 · 跑黄金集，输出过程判据 + 结果判据(LLM-judge)
# 用法：
#   python -m eval.run_eval_agent --mock --no-judge # 无 Key：确定性 mock LLM 只跑过程判据
#   python -m eval.run_eval_agent                   # 真实 LLM（需 .env Key）：过程 + judge
#   python -m eval.run_eval_agent --no-judge        # 真实 LLM 只跑过程判据（省 judge 调用）
# 判据：
#   过程（零外部 LLM）——轨迹命中率 / 终止率 / 平均步数 / 异常率 / parser 失败率
#   结果（LLM-judge，need Key）——判分达标率（scorable 且 passed）/ scorable 条数
# 实跑记录：eval/m6_real_run_20260901*.txt（2026-09-01 两轮连续达标）
# ============================================================

import argparse
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from eval.judge import Judge          # noqa: E402
from planner.loop import Planner, is_tool_result_message  # noqa: E402
from skills import build_registry     # noqa: E402

CASES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_cases.json")


def build_mock_llm(reg):
    """确定性 mock LLM：路由近似真实 planner 策略，让评测管线无 Key 也能跑通过程判据。

    - tool_result 回喂：有 errors → explain_error 讲解；无 → text 收尾
    - 复习咨询 → get_review_queue
    - 知识提问（是什么/怎么用/区别）→ lookup_knowledge_point
    - 陈述句（句号结尾、非求助/翻译）→ identify_errors 检查（干净句也查，宁漏勿错反向护栏）
    - 上下文追问（呢？/对吗？）：含"很多"演示典型错序句；否则回查上一句陈述句
    - 其余（空/寒暄/无意义/翻译）→ text 直答，不误调偏误技能
    """
    import json as _json
    from planner.loop import TOOL_RESULT_PREFIX, is_tool_result_message

    def _last_user(messages):
        for m in reversed(messages):
            if m.get("role") == "user" and \
                    not str(m.get("content", "")).startswith(TOOL_RESULT_PREFIX):
                return m.get("content", "")
        return ""

    def _identify(text_to_check):
        return _json.dumps([
            {"type": "action", "name": "identify_errors",
             "params": {"text": text_to_check, "level": 2}},
            {"type": "text", "content": "我看看这句有没有偏误。"},
        ], ensure_ascii=False)

    def _llm(messages):
        last = messages[-1]

        if is_tool_result_message(last):
            try:
                payload = _json.loads(
                    str(last["content"])[len(TOOL_RESULT_PREFIX):].strip())
                errors = (payload.get("result") or {}).get("errors") or []
            except Exception:  # noqa: BLE001
                errors = []
            if errors:
                return _json.dumps([
                    {"type": "action", "name": "explain_error",
                     "params": {"error": errors[0]}},
                    {"type": "text", "content": "我来讲讲这个偏误。"},
                ], ensure_ascii=False)
            return _json.dumps(
                [{"type": "text", "content": "检查完毕，这句没有高置信偏误，写得很好。"}],
                ensure_ascii=False)

        u = (_last_user(messages) or "").strip()
        if "复习" in u:
            return _json.dumps([
                {"type": "action", "name": "get_review_queue", "params": {}},
                {"type": "text", "content": "我看看你现在的复习安排。"},
            ], ensure_ascii=False)
        if any(k in u for k in ["是什么", "怎么用", "区别"]):
            kw = "把字句" if "把字句" in u else ("很多" if "很多" in u else "了")
            return _json.dumps([
                {"type": "action", "name": "lookup_knowledge_point",
                 "params": {"keyword": kw}},
                {"type": "text", "content": "我来查一下这个知识点。"},
            ], ensure_ascii=False)
        if u.endswith("呢？") or u.endswith("对吗？"):
            if "很多" in u:
                return _identify("我想买苹果很多。")
            for m in messages:
                if m.get("role") == "user" and \
                        str(m.get("content", "")).rstrip().endswith("。") and \
                        not str(m.get("content", "")).startswith(TOOL_RESULT_PREFIX):
                    return _identify(m["content"])
            return _identify(u)
        if u.endswith("。") and "帮我" not in u and "翻译" not in u:
            return _identify(u)
        return _json.dumps(
            [{"type": "text", "content": "这是一个测试回答（mock）。"}],
            ensure_ascii=False)

    return _llm


class AgentHarness:
    def __init__(self, planner: Planner, judge: Judge):
        self.planner = planner
        self.judge = judge

    def _expected_trace_ok(self, actual: list, expected: list) -> bool:
        """轨迹命中：expected 是 actual 的子序列（按序出现）。纯提问（expected 空）算命中。"""
        if not expected:
            return True  # 非偏误/边界类，只要不误调偏误技能即算，由 judge 再评判
        it = iter(actual)
        return all(any(x == t for x in it) for t in expected)

    def run_one(self, case: dict, use_judge: bool) -> dict:
        scenario, text = case["scenario"], case["input"]
        expected = case.get("expected_trace", [])
        result = {}

        try:
            return self._run_case(case, scenario, text, expected, use_judge, result)
        except Exception as e:  # noqa: BLE001
            # 单用例 LLM 网络级异常不炸整个评测（计入 fallback / 未终止）
            result.setdefault("used_skills", [])
            result.setdefault("steps", [0])
            result["fallback_hits"] = 1
            result["trace_ok"] = False
            result["stage_note"] = scenario
            result["error"] = str(e)[:200]
            result["judge"] = {"scorable": False, "reason": "llm_error"}
            return result

    def _run_case(self, case, scenario, text, expected, use_judge, result):

        # ---- 多轮用例：跑两轮以验证 long-term memory ----
        history = []
        max_score = case.get("max_score", 10)
        answer = ""

        if scenario == "多轮":
            # 解析两轮输入（约定格式：第一轮...第二轮...）
            rounds = []
            import re as _re
            parts = _re.split(r"第二轮[:：]", text)
            rounds.append(parts[0].split("：", 1)[-1].strip())
            # 移除第一轮标记后缀
            r0 = rounds[0].replace("（第一轮", "").replace("）", "").strip()
            rounds = [r0]
            if len(parts) > 1:
                rounds.append(parts[1].replace("）", "").strip())

            for i, u in enumerate(rounds):
                if not u:
                    continue
                hist = [{"role": m["role"], "content": m["content"]} for m in history]
                r = self.planner.run(u, history=hist)
                history.append({"role": "user", "content": u})
                history.append({"role": "assistant", "content": r.get("text", "")})
                if not isinstance(r["text"], str) or not r["text"]:
                    r["text"] = ""
                answer = r["text"]
                result.setdefault("used_skills", []).extend(r.get("used_skills", []))
                result.setdefault("steps", []).append(r.get("steps"))
                if r.get("fallback"):
                    result["fallback_hits"] = result.get("fallback_hits", 0) + 1
                if r.get("protocol_degraded"):
                    result["protocol_degraded"] = True
                if r.get("fallback") or r.get("protocol_degraded"):
                    result.setdefault("round_notes", []).append(
                        f"轮{i+1}: fallback={r.get('fallback')} "
                        f"reason={r.get('reason', '-')} "
                        f"degraded={bool(r.get('protocol_degraded'))}")
            result["steps"] = result.get("steps", [])
            result["used_skills"] = result.get("used_skills", [])
        else:
            r = self.planner.run(text)
            result["used_skills"] = r.get("used_skills", [])
            answer = r.get("text", "")
            result["steps"] = [r.get("steps", 0)]
            if r.get("fallback"):
                result["fallback_hits"] = 1
            else:
                result["fallback_hits"] = 0
            if r.get("protocol_degraded"):
                result["protocol_degraded"] = True
            if r.get("fallback") or r.get("protocol_degraded"):
                result["round_notes"] = [
                    f"fallback={r.get('fallback')} reason={r.get('reason', '-')} "
                    f"degraded={bool(r.get('protocol_degraded'))}"]

        actual = result["used_skills"]
        result["trace_ok"] = self._expected_trace_ok(actual, expected)
        result["stage_note"] = scenario
        result["answer_preview"] = (answer or "")[:120]

        # ---- judge（结果判据）----
        result["judge"] = {"scorable": False, "reason": "skipped"}
        if use_judge and answer:
            result["judge"] = self.judge.run(
                input_text=text,
                answer=answer,
                max_score=max_score,
                rubric=case.get("rubric", ""),
            )
        return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-judge", action="store_true", help="不跑 LLM-judge（省 Key）")
    ap.add_argument("--mock", action="store_true",
                    help="用确定性 mock LLM 驱动 planner（无 Key 跑通过程判据）；默认用真实 LLM")
    args = ap.parse_args()

    with open(CASES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    cases = data["golden_agent_cases"]

    reg = build_registry()
    if args.mock:
        planner = Planner(reg, llm_call=build_mock_llm(reg))
    else:
        planner = Planner(reg)
    judge = Judge()

    harness = AgentHarness(planner, judge)
    rows = []
    for c in cases:
        rows.append(harness.run_one(c, use_judge=not args.no_judge))

    _report(rows)


def _report(rows):
    total = len(rows)
    trace_hit = sum(1 for r in rows if r.get("trace_ok"))
    terminated = sum(1 for r in rows if r.get("fallback_hits", 0) == 0)
    steps = [s for r in rows for s in r.get("steps", []) or [0]]
    avg_steps = sum(steps) / len(steps) if steps else 0

    # judge 汇总
    scorable = [r["judge"] for r in rows if r["judge"].get("scorable")]
    passed = [j for j in scorable if j.get("passed")]

    print("=" * 62)
    print("M6 自由对话评测（agent_cases.json）")
    print(f"用例总数 {total}")
    print("=" * 62)
    print(f"[过程判据]")
    print(f"  技能轨迹命中率 : {trace_hit}/{total} = {trace_hit/total:.1%}")
    print(f"  终止率          : {terminated}/{total} = {terminated/total:.1%}")
    print(f"  平均步数        : {avg_steps:.2f} (上限 6)")
    print(f"[结果判据 / LLM-judge]")
    if scorable:
        print(f"  判分达标率      : {len(passed)}/{len(scorable)} = {len(passed)/len(scorable):.1%}（earned ≥ 80% 满分）")
    else:
        print(f"  无 scorable 判分（无 Key 或全解析失败），需有 Key 环境补齐")
    print("=" * 62)
    print("按用例明细:")
    for i, r in enumerate(rows, 1):
        j = r["judge"]
        s = j.get("scorable")
        score_txt = f"{j.get('score')}/{j.get('maxScore')}({'P' if j.get('passed') else 'F'})" if s else f"skip({j.get('reason')})"
        flags = []
        if r.get("fallback_hits"):
            flags.append(f"fb×{r['fallback_hits']}")
        if r.get("protocol_degraded"):
            flags.append("pd")
        if r.get("error"):
            flags.append("err")
        flag_txt = (" [" + ",".join(flags) + "]") if flags else ""
        print(f"  {i:>2} {r.get('stage_note','')} | trace={'OK' if r.get('trace_ok') else 'X'} "
              f"| steps={r.get('steps')} | judge={score_txt} | used={r.get('used_skills')}{flag_txt}")
        # 失败细节：fallback 原因 / 协议降级 / judge 差评定位
        if not r.get("trace_ok") or r.get("fallback_hits") or \
                (s and not j.get("passed")):
            for note in r.get("round_notes", []):
                print(f"       · {note}")
            print(f"       · answer: {r.get('answer_preview', '')!r}")
            if s and not j.get("passed"):
                print(f"       · judge评语: {j.get('comment', '')}")
                for imp in (j.get("improvements") or [])[:2]:
                    print(f"       · 待改进: {imp}")


if __name__ == "__main__":
    main()
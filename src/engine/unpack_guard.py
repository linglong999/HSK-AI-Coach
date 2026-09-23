# -*- coding: utf-8 -*-
# engine/unpack_guard.py —— B5 I4：超纲事后拦截环（方案 3 事后环 · 确定性硬查）
#
# 架构（总纲 §1：超纲确定性）：编程判断"解包里是否混进超等级词"——解包输出
# tokenize 后查**当前生效词表**硬查等级（lexicon_hsk1_4.json；B8 完成后随
# recognizer 换源 lexicon_hsk3_2025.json）。**复用 recognizer.detect_beyond_level
# 零改动**，不新建 LLM 友好文件（超纲检测 = 查表，勿与"全文难度评分"混淆）。
#
# 拦截环挂 explain 调用点之后（serve/dialog_run 讲解出口），**非 explainer 内部**
# ——explainer 保持纯生成，守门员独立（与 B1 诊断器同一架构哲学：确定性守门、
# LLM 只管生成）。覆盖两讲解出口：explainer.explain 常规讲解 与 B2 explain_error
# 深攻讲解同过守门（深攻讲解不吃超纲豁免）。
#
# 处置（B5 拍板点 2）：带违规清单重试 1 次、再漏则标注降级呈现（非无限重试：
# 无限重试=延迟成本失控；只标注=放任超纲呈现违背"≤HSK4 呈现标准"）。

from typing import Callable, Dict, List, Optional

from engine.recognizer import detect_beyond_level, load_lexicon


def check_unpack(explained_text: str, learner_level: int, lex: dict) -> dict:
    """方案 3 事后环：解包文本 tokenize → detect_beyond_level 硬查 → 违规词清单。
    返回 {"ok": bool, "violations": [{word, level}]}。
    ok=False 当且仅当违规非空。空文本/无词表 → ok=True（无可查则视为无违规，
    不误伤；词表缺失时 detect_beyond_level 内已降级返回空）。"""
    violations = detect_beyond_level(explained_text or "", learner_level, lex)
    return {"ok": not violations, "violations": violations}


def _format_violations(violations: List[dict]) -> str:
    """违规清单 → 注入 prompt 的约束文本（供重试注入）。"""
    return "、".join(f"{v['word']}(HSK{v.get('level', '?')})" for v in violations)


def guard_unpack(generate: Callable[..., dict],
                 learner_level: int,
                 lex: Optional[dict] = None,
                 retries: int = 1,
                 ) -> dict:
    """事后拦截环编排：生成 → 硬查 → 违规时带清单重试 `retries` 次 → 再漏降级标注。

    generate: Callable[[List[dict]], dict]——产出含 "explanation" 的讲解结果 dict；
      入参为上一轮违规清单（首轮为 []，可注入"这些词超纲禁用：…"）；返回结果透出。
    learner_level: 学习者等级（int，detect_beyond_level 用）。
    lex: 词表 dict；None → recognizer.load_lexicon() 单点取表（B8 换源零散改）。
    retries: 违规后最多重试次数（拍板点 2 = 1）。

    返回 {"result": dict, "ok": bool, "attempts": int, "violations": list}：
      - ok=True：生成未被拦截（result 为最终讲解）；violations=[]。
      - ok=False：retries 次重试仍违规 → 降级：result 追加
        "degraded_overscope": True 与 "overscope_violations": list（前端据此标注
        "含超纲词" + 剥离违规词域兜底呈现）；violations=list。
    """
    lex = lex if lex is not None else load_lexicon()
    attempts = 0
    pending: List[dict] = []
    while True:
        attempts += 1
        result = dict(generate(pending) or {})
        check = check_unpack(result.get("explanation", ""), learner_level, lex)
        if check["ok"]:
            return {"result": result, "ok": True, "attempts": attempts,
                    "violations": []}
        pending = check["violations"]
        if attempts > retries:   # 已重试 retries 次仍违规 → 降级
            result["degraded_overscope"] = True
            result["overscope_violations"] = [
                {"word": v["word"], "level": v.get("level")} for v in pending]
            return {"result": result, "ok": False, "attempts": attempts,
                    "violations": pending}
        # 未超限 → 带违规清单注入重试（generate 由调用方负责把 pending 写进 prompt）
        result["_retry_violations"] = _format_violations(pending)
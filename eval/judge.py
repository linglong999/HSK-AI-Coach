# ============================================================
# eval/judge.py
# M6 自由对话评测 · LLM 判分器
# 判分结构（决策乙）：{score, maxScore, passed, comment, strengths, improvements, scorable}
# 核心取舍（守宁漏勿错，偏离 OpenMAIC quiz-grade）：
#   - JSON 解析失败 → scorable:false，不记入达标率，绝不臆造一半分
#   - passed 由代码层算（score/maxScore >= 0.8），不信任 LLM 自报
# ============================================================

import json
import os
import re
import sys
from typing import Any, Dict, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

PASS_RATIO = 0.8


class Judge:
    """对自由对话的最终答案做诊断型判分（LLM-judge）。

    需要 API Key。无 Key 或解析失败均返回 scorable:false，不产生臆造分数。
    """

    SYSTEM_PROMPT = (
        "你是 HSK 中文教学评分员。根据评分要点（rubric）对学生的回答或助教给出的讲解打分。\n"
        "只输出一个 JSON 对象，不要任何其他文字：\n"
        "{\"score\": 0..满分整数, \"comment\": \"一两句评语\", "
        "\"strengths\": [\"优点1\", ...], \"improvements\": [\"待改进1\", ...]}\n"
        "score 必须诚实：能力不足不能给满分，也不因格式问题乱扣分。"
    )

    def __init__(self, llm=None, pass_ratio: float = PASS_RATIO):
        """llm: 可注入的 callable([messages])->str；None→用 LLMClient.chat 默认。"""
        self._llm = llm
        self.pass_ratio = pass_ratio

    def _default_llm(self, messages) -> str:
        from engine.llm.client import LLMClient
        return LLMClient().chat(messages, temperature=0.2)

    def run(self, input_text: str, answer: str, max_score: int = 10,
            rubric: str = "") -> Dict[str, Any]:
        """对一次回答判分。返回诊断型结构。"""
        user_prompt = (
            f"题目：{input_text}\n"
            f"满分：{max_score}分\n"
            f"评分要点：{rubric or '给出清晰、准确、有帮助的回答'}\n"
            f"待评答案：{answer}"
        )
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            raw = self._invoke(messages)
        except RuntimeError as e:
            # 无 Key → 诚实标注跳过，不伪造
            return self._unscorable(max_score, reason="no_key", detail=str(e))

        parsed = self._parse_json(raw)
        if parsed is None:
            # 解析失败：不臆造分数（宁漏勿错）
            return self._unscorable(max_score, reason="parse_fail", detail=raw)

        try:
            score = int(parsed.get("score"))
        except (TypeError, ValueError):
            return self._unscorable(max_score, reason="bad_score")

        score = max(0, min(max_score, score))
        passed = (score / max_score) >= self.pass_ratio if max_score > 0 else False

        return {
            "score": score,
            "maxScore": max_score,
            "passed": passed,
            "comment": str(parsed.get("comment") or ""),
            "strengths": parsed.get("strengths") or [],
            "improvements": parsed.get("improvements") or [],
            "scorable": True,
            "reason": "ok",
        }

    # ---------------- 内部 ----------------
    def _invoke(self, messages) -> str:
        if self._llm is not None:
            return self._llm(messages)
        return self._default_llm(messages)

    def _parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        """从 LLM 文本提取 JSON 对象（仿 OpenMAIC：找第一个 {..}、多候选），失败 None。"""
        if not text:
            return None
        candidates = []
        # 1) 剥代码围栏整段
        fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
        if fenced:
            candidates.append(fenced.group(1))
        # 2) 从首个 { 到末个 }
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            candidates.append(m.group(0))
        for cand in candidates:
            try:
                obj = json.loads(cand)
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    def _unscorable(self, max_score: int, reason: str, detail: str = "") -> Dict[str, Any]:
        return {
            "score": None,
            "maxScore": max_score,
            "passed": False,
            "comment": f"（无法判分：{('跳过' if reason == 'no_key' else '解析失败')}）",
            "strengths": [],
            "improvements": [],
            "scorable": False,
            "reason": reason,
            "detail": detail[:200],
        }
# ============================================================
# Skill: identify_errors（skills/identify_errors.py）
# M1 · 薄壳包 Recognizer.recognize()，不改引擎
# - 入参映射：text → text, level → level（默认 HSK3，注入非自取）
# - 出参封装：对齐 JSON 接缝契约 v1（errors/uncertain/degraded）
# ============================================================

from typing import Any, Dict, Optional

from skills.base import Skill, normalize_level_int


class IdentifyErrorsSkill(Skill):
    metadata: Dict[str, Any] = {
        "name": "identify_errors",
        "version": "0.1.0",
        "summary": "识别一句话里的中文偏误并分类（词汇/语法/语用/汉字），返回错误片段与修正建议。",
        "triggers": [
            "学习者给出一句中文，问'这句话有错吗/对不对/帮我看看帮我改'",
            "需要判断一句话是否存在偏误、偏误在哪、该怎么改",
        ],
        "guardrails": [
            "不应仅在求翻译、求点评、求作文润色时触发（这些不是找偏误）",
            "不应在问单一知识点定义（如'把字句是什么'）时触发——那是 lookup_knowledge_point",
        ],
        "input": {
            "text": "必填，待检查的中文句子整数，如 '我想买苹果很多。'",
            "level": "可选，学习者 HSK 等级（1-4），默认 'HSK3'；由调用方注入，技能不自行获取上下文",
        },
        "output": "对齐契约 v1：errors[]（fragment/correction/type/confidence/knowledge_point_id）+ uncertain[] + degraded[] + hypotheses[]（0.22 L1 迁移候选假设，只读不写图谱）",
    }
    input_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "level": {"type": "string"},
        },
        "required": ["text"],
    }
    output_schema: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "errors": {"type": "array"},
            "uncertain": {"type": "array"},
            "degraded": {"type": "array"},
            "hypotheses": {"type": "array"},
        },
    }

    def __init__(self, recognizer=None, graph=None):
        super().__init__()
        self._recognizer = recognizer
        self._graph = graph

    def _get_recognizer(self):
        if self._recognizer is None:
            from engine.recognizer import Recognizer
            self._recognizer = Recognizer()
        return self._recognizer

    def _write_graph(self, text: str, level: int, errors: list,
                     uncertain: list) -> list:
        """图谱写入（0.17 §6 约束在数据写）：语义与 router.process 完全一致——
        确认偏误 upsert 节点、uncertain 入待确认队列、同句 ≥2 KP 建混淆边。
        event_key=text 幂等（同句重放不重复计数）。写失败仅记 degraded，不阻塞识别结果。"""
        degraded = []
        level_label = f"HSK{level}"
        for i, err in enumerate(errors):
            try:
                err["graph_write"] = self._graph.ingest_error(
                    {**err, "sentence": text, "level": level_label},
                    f"{text}#err{i}")
            except Exception as e:  # noqa: BLE001
                err["graph_write"] = {"status": f"write_failed:{e}"}
                degraded.append(f"graph_write: {e}")
        for i, u in enumerate(uncertain):
            try:
                self._graph.ingest_error({**u, "sentence": text, "uncertain": True},
                                         f"{text}#unc{i}")
            except Exception as e:  # noqa: BLE001
                degraded.append(f"graph_write: {e}")
        kps = [e.get("knowledge_point_id") for e in errors if e.get("knowledge_point_id")]
        if kps:
            try:
                self._graph.link_errors_in_sentence(kps, f"{text}#sentence")
            except Exception as e:  # noqa: BLE001
                degraded.append(f"confusion_edge: {e}")
        try:
            self._graph.save()
        except Exception as e:  # noqa: BLE001
            degraded.append(f"graph_save: {e}")
        return degraded

    def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        text = str(context.get("text", "")).strip()
        if not text:
            return {"errors": [], "uncertain": [], "degraded": [],
                    "_error": "缺少必填入参 text"}

        # level 归一：契约接受 int（1-4）；入参可能是 int / '3' / 'HSK3'
        level = normalize_level_int(context.get("level", 3))

        rec = self._get_recognizer()
        result = rec.recognize(text, level=level,
                               native_lang=str(context.get("native_lang", "")))
        errors = result.get("errors", [])
        uncertain = result.get("uncertain", [])
        rd = result.get("degraded")
        if isinstance(rd, list):
            degraded = [str(x) for x in rd]
        else:
            degraded = [str(rd)] if rd else []

        # 图谱写入（仅注入 graph 时；对话模式地图照常生长）
        if self._graph is not None and (errors or uncertain):
            degraded.extend(self._write_graph(text, level, errors, uncertain))

        # 契约 v1 对齐：recognize 结果须含 errors/uncertain/degraded 键，缺省补全
        # hypotheses[]（0.22 L1 迁移假设）：仅确认层偏误的候选归因，不进图谱（_write_graph 只读 errors/uncertain）
        return {
            "errors": errors,
            "uncertain": uncertain,
            "degraded": degraded,
            "hypotheses": result.get("hypotheses", []) if isinstance(result, dict) else [],
            "_level_used": level,
        }
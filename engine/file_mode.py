# ============================================================
# 文件讲解通道（模式二，2.5 交互 + 2.6 数据通道）
# 复用 识别引擎(Recognizer) + 讲解引擎(Explainer) + 图谱(ErrorGraph)，不重写。
# 分两路：
#  - 选中片段含偏误 → 纠正链：识别→费曼讲解→图谱沉淀（source=file_mode 标记来源）
#  - 选中片段为正确句子/短语 → 泛讲解：解释含义 + 语言点，不写图谱（正确句不污染）
# 触发粒度：选中哪讲哪（整句/短语按选区）
# ============================================================

import os
import sys
from typing import Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.recognizer import Recognizer
from engine.explainer import Explainer
from engine.graph.error_graph import ErrorGraph
from engine.llm.client import LLMClient, JSONStrictError

SYSTEM_PLAIN = """【角色】你是一位温和的中文教师，服务于 HSK{level} 级、母语为{native_language}的学习者。
【任务】用"为什么 + 例子"的浅显语言，解释下面这段中文的含义和值得注意的语言点。不要挑错、不要纠偏——这段是正确的中文，我只需要理解它。
【约束】1. 用简短例子帮助理解。2. 分层说清"整体意思"和"值得注意的词/结构"两大部分。3. 语言点各配一个小例子。4. 每个值得注意的语言点都要拆成独立的 key_point，key_points 必须不少于 3 条。5. 输出严格 JSON。
【双语策略（统一）】讲解正文一律以中文为主。允许出现学习者母语（记为 M），但只限两类：(a) 括号锚定——某中文词/结构对 M 学习者难懂、且 M 有直接对应时，可写「中文词（M 短对应）」帮助理解；(b) 跨语言对照——讲中英语言或表达方式差异、且一句 M 能点透差异时，可短引一句 M 作对照（整句至多一句，且必须紧扣当前语言点）。除此之外禁止随机夹杂 M、禁止用 M 整段讲解、禁止中文与 M 碎片式混排。
【输出格式】{{"explanation": "通俗易懂的讲解（含整体意思 + 值得注意的语言点 + 例子）", "key_points": [{{"text": "一个可独立判定的要点（我复述时能否体现就看它）"}}]}}"""


class FileCoach:
    """文件讲解通道：选中哪讲哪。含偏误走纠正链，正确走泛讲解。"""

    def __init__(self, learner_id: str = "file_user", native_lang: str = "",
                 user_level: str = "HSK3", source: str = "file_mode"):
        self.learner_id = learner_id
        self.native_lang = native_lang
        self.user_level = user_level
        self.source = source
        self.recognizer = Recognizer()
        self.explainer = Explainer()
        self.graph = ErrorGraph(learner_id)
        self.graph.load()
        self._client = LLMClient()

    def explain_selection(self, selection: str, sentence: str = "",
                          event_key: str = "", commit_graph: bool = True) -> dict:
        """处理一次选中（2.6 数据通道）。返回识别+讲解结果。
        selection: 用户选中的片段；sentence: 所在句（可选，偏误讲解用原句上下文）。
        """
        selection = (selection or "").strip()
        if not selection:
            return {"selection": "", "errors": [], "plain": None,
                    "has_error": False, "note": "空选中"}

        result = {"selection": selection, "errors": [], "plain": None,
                  "has_error": False}
        ctx = sentence or selection

        # 0.22：native_lang 传入识别（迁移假设 + 母语上下文；与 Router.process 同步补漏）
        recog = self.recognizer.recognize(selection, native_lang=self.native_lang)
        confirmed = recog.get("errors", [])

        if confirmed:
            result["has_error"] = True
            for i, err in enumerate(confirmed):
                err = {**err, "sentence": ctx, "source": self.source}
                try:
                    expl = self.explainer.explain(
                        err, user_level=self.user_level, native_lang=self.native_lang)
                except Exception as e:
                    expl = {"_degraded": True, "explanation": f"讲解降级: {e}"}
                gwrite = None
                if commit_graph:
                    try:
                        gwrite = self.graph.ingest_error(
                            {**err, "sentence": ctx},
                            f"{event_key or selection}#file{i}")
                    except Exception as e:
                        gwrite = {"status": f"write_failed:{e}"}
                result["errors"].append({"error": err, "explanation": expl,
                                         "graph_write": gwrite})
        else:
            result["plain"] = self._explain_plain(selection)

        if commit_graph:
            self.graph.save()
        return result

    def _explain_plain(self, text: str) -> dict:
        """正确片段的泛讲解（不写图谱）。"""
        sys_p = SYSTEM_PLAIN.format(level=self.user_level[3:] or "3",
                                    native_language=self.native_lang or "未知")
        try:
            raw = self._client.chat_json_strict(
                sys_p, f"请解释这段中文：\n\n{text}", temperature=0.4)
        except JSONStrictError:
            return {"explanation": f"（泛讲解降级）这段中文的正确理解见原文：{text}",
                    "key_points": []}
        return {
            "explanation": raw.get("explanation", ""),
            "key_points": [p if isinstance(p, dict) else {"text": p}
                           for p in raw.get("key_points", []) if p],
        }

    def review_queue(self):
        return self.graph.get_review_queue()
# ============================================================
# 偏误识别引擎（recognizer）
# 对齐 2.1 v0.3 定稿 schema（fragment/correction/type/type_confident/confidence/knowledge_point_id）
# 领域层创新：按 HSK 知识清单约束知识库候选、置信度分级（防过度纠正）
# 配合 knowledge_point_id：识别时注入清单，LLM 选最贴切 kp，合并层校验命中
# ============================================================

import json
import os
import sys

# 项目根 = HSK-AI-Coach/（recognizer.py 在 engine/ 下）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
from engine.llm.client import LLMClient
from engine.transfer import match as transfer_match

# 知识清单路径（HSK 1-4 级 MVP 骨架版）：位于项目根（HSK-AI-Coach/）datasets/
KNOWLEDGE_POINTS_PATH = os.path.join(_PROJECT_ROOT, "datasets", "knowledge_points_v1_4.json")
# 权威 lexicon 路径（HSK1-4 词表+字表，GF 0025-2021，超纲判定真源）
LEXICON_PATH = os.path.join(_PROJECT_ROOT, "datasets", "lexicon_hsk1_4.json")

# 主识别提示词——对齐 2.1 v0.3 定稿（Prompt 六要素 + knowledge_point_id 候选）
SYSTEM_PROMPT = """【角色】你是一个 HSK 偏误识别器。任务是从给定的中文句子中识别语言偏误并输出结构化结果。全程不要寒暄、不要评价、不要附加任何人格，只输出结果。

【任务】给定一句中文，找出其中真实存在的偏误，输出结构化偏误列表。类型限于：词汇 | 语法 | 语用 | 汉字（语音当前不启用，预留）。对每个偏误给出 confidence 初值（0-1）与最贴切的 HSK 知识点 id（knowledge_point_id）。

【上下文】
- HSK 知识点清单（供 knowledge_point_id 选择，必须从下面清单中选最贴切者）：
{knowledge_tree_portal}

【约束】
1. 只识别真实存在的偏误，不吹毛求疵；无偏误则返回空列表。
2. 分类仅限上述类型枚举，不要自造。
3. beyond_level 不由你判断（由规则层对照词表确定性计算），你只需正常报告偏误。
4. 每个偏误给 confidence 初值（0-1）；最终是否"待确认"由置信度决策序列决定，不要在此自行下结论。
5. 修正建议要符合学习者当前等级，不教超纲内容。
6. 每个偏误给出 knowledge_point_id（从清单中选最贴切者）；清单内无对应项则置空字符串。
7. fragment 只圈发生偏误的最小片段：不含该偏误前后"看起来相关但本身正确"的词，禁止把整个句子或整块短语当 fragment。但**最小 ≠ 残缺**——fragment 必须覆盖让该偏误成立的完整机制，不得把机制本身诱发词也切掉：例如「他常常迟到了」的偏误是"频率副词与'了'冲突"，fragment 应含「常常迟到了」而非仅「迟到了」。只有明显独立的正确成分才 cut（连词/语气词/名词尾等），如「虽然他很累，但是他去上班」缺衔接时 fragment 是「他去上班」而非「但是他去上班」；「她是个很好得人」的错处 fragment 是「很好得」而非「很好得人」。

【隐性偏误示例（few-shot）——校准"看似合格实则偏误"的判断】
以下示例教你识别三类隐性偏误（表层语法接近合格、依赖语用/搭配/语体知识的偏误），但**切勿当匹配模板**：仅当句子同时满足每条标注的"判定条件"时才识别，否则宁可不报（避免过度纠正）。示例与待评句子可能同型却不同词，请按偏误"机制"判断，不要按字面匹配。反例用于界定合法边界，同样不要漏过。

例1（语用：语气词"吧"误用于直接是非问）
判定条件：句子是直接向对方发问、期待"是/否"或明确表态的是非疑问，却用"吧"收尾；若"吧"表提议/揣测/商量则为合法，不报。
输入：这个好吃吧？（上下文：只是单纯问对方好不好吃）
输出：{{"fragment":"吧","correction":"吗","type":"语用","type_confident":true,"confidence":0.85,"knowledge_point_id":""}}
反例（合法，不报）：你好，喝杯水吧。（"吧"表提议）

例2（语用/量词：名词与该名词专属量词误配）
判定条件：句中名词在汉语里有更贴切的专用量词（电影→部、手机→部、书→本、电视→台），学习者却泛化用"个"；若名词本用"个"或用词正确，则不报。
输入：他买了一个手机。
输出：{{"fragment":"一个手机","correction":"一部手机","type":"语法","type_confident":true,"confidence":0.9,"knowledge_point_id":"kp-liangci"}}
反例（合法，不报）：他吃了一个苹果。（"苹果"用"个"，正确）

例3（语用：语体失衡）
判定条件：问询对象明确为成年人/正式或陌生场合，却用了只宜问幼童的"几岁"；若对象是小孩则合法，不报。
输入：老师，你几岁？（对象为成年老师）
输出：{{"fragment":"几岁","correction":"您多大年纪","type":"语用","type_confident":true,"confidence":0.8,"knowledge_point_id":""}}
反例（合法，不报）：小朋友，你几岁啦？（对象是小孩）

例4（词汇/搭配：液体饮品用"喝"）
判定条件：宾语为不可咀嚼的液体饮品（奶茶、咖啡、茶、水、酒、果汁等），却用"吃"；仅当宾语是可咀嚼的固体食物（苹果、饭、饼干、菜）时"吃"才合适。
输入：我想吃奶茶。
输出：{{"fragment":"吃奶茶","correction":"喝奶茶","type":"词汇","type_confident":true,"confidence":0.8,"knowledge_point_id":""}}
反例（合法，不报）：我想吃苹果。（苹果可咀嚼，"吃"正确）

例5（语法/语序：数量词"很多"作定语却后置）
判定条件：句中"很多"修饰一个此前已明确的名词，却置于句末或谓语之后造成定语后置（如"我想要奶茶，很多"→ 应"很多奶茶"）；若"很多"作谓语表数量（"这家店人很多"）或作独立表语，则位置合法，不报。
输入：我想要奶茶，很多。
输出：{{"fragment":"很多","correction":"很多奶茶","type":"语法","type_confident":true,"confidence":0.75,"knowledge_point_id":""}}
反例（合法，不报）：这家店人很多，所以排了很长的队。（"很多"作谓语表数量）

【输出格式】严格输出 JSON，不要输出任何其他文字：
{{"errors": [{{"fragment": "偏误片段", "correction": "修正建议", "type": "词汇|语法|语用|汉字", "type_confident": true, "confidence": 0.0-1.0, "knowledge_point_id": "<id 或空>"}}]}}"""


def load_knowledge_points(path: str = KNOWLEDGE_POINTS_PATH) -> dict:
    """加载 HSK 知识清单，返回 {id: 知识点dict}。失败返回空 dict（不阻断）"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("knowledge_points", {})
    except Exception as e:
        print(f"[recognizer] 知识清单加载失败({path}): {e}（降级为空清单）")
        return {}


def load_lexicon(path: str = LEXICON_PATH) -> dict:
    """加载权威 HSK 词表/字表，返回 {word_level:{}, char_level:{}}。失败返回空（不阻断）"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {"word_level": data.get("word_level", {}),
                "char_level": data.get("char_level", {})}
    except Exception as e:
        print(f"[recognizer] 权威词表加载失败({path}): {e}（降级：超纲判定停用）")
        return {"word_level": {}, "char_level": {}}


def detect_beyond_level(text: str, learner_level: int, lex: dict) -> list:
    """确定性超纲判定（2.1 v0.3：beyond_level 由规则层对照词表算，不靠 LLM）。

    对句子文本，扫描长词匹配词表（最长匹配，避免单字切碎），取其中高于学习者等级的词。
    beyond_level 语义：句子使用了超出学习者等级的词（宽容相邻一级：>learner+1 才算超纲）。
    返回 [{word,level}...]，空=无超纲。
    """
    if not text or learner_level < 1:
        return []
    word_level, char_level = lex["word_level"], lex["char_level"]
    hits = []          # (word, level) 去重
    seen = set()
    # 词表最长匹配：按等级倒序贪心，优先标更高的超纲词
    # 先将文本中所有在词表里的词收集（简单实现：逐字做正向最长匹配）
    i, n = 0, len(text)
    while i < n:
        matched = None
        # 去词表找以 text[i] 起头的词（先试较长，最多 6 字）
        for L in range(min(6, n - i), 0, -1):
            seg = text[i:i + L]
            if seg in word_level:
                matched = seg
                break
        if matched:
            lvl = word_level[matched]
            if lvl > learner_level + 1:   # 宽容相邻级，>learner+1 才算超纲
                key = f"w:{matched}"
                if key not in seen:
                    seen.add(key)
                    hits.append({"word": matched, "level": lvl})
            i += len(matched)
        else:
            i += 1
    return hits


def build_portal(kps: dict) -> str:
    """把清单编译成注入 prompt 的候选文本（id + 知识点名 + 等级）"""
    if not kps:
        return "（无可用清单，knowledge_point_id 置空）"
    lines = []
    for kp_id, kp in kps.items():
        lines.append(f"- {kp_id}：{kp.get('knowledge_point', '')}（HSK{kp.get('level', '?')}级）")
    return "\n".join(lines)


# 处置义把字句的结果/完成标记（把+宾语+处置动词+…该标记 → 合法处置式）
_BA_PREP = ("在", "到", "给")       # 出现在"把"后多为语序错位（PP 前置动词），不撤
_BA_TAIL = ("了", "完", "掉", "走", "成", "干净", "清楚", "好")

# 可"无地直接作状语"的双音节形容词/情状词（"地"可省可不省，缺地不构成偏误）——3.5-CLN-016
# 用于撤单"状中结构缺'地'"的过度纠正：原文校验修正仅是在该词后插"地"才撤，否则保留
_NO_DE_ADV = ("认真", "努力", "仔细", "积极", "主动", "彻底", "慢慢", "好好", "真正", "完全", "经常")


def _is_legal_ba_disposal(sentence: str) -> bool:
    """确定性护栏（约束7 补洞，3.5-CLN-019）：判定是否为"合法处置义把字句"被误报。
    满足：含"把"；"把"后无 [在/到/给] 处所介词(那是语序错位特征)；且宾语后处置动词带结果/完成标记。
    命中 → 合法处置式，撤单；否则保留（可能是真把字句偏误，如"把书在桌子上放了"）。"""
    if "把" not in sentence:
        return False
    after = sentence.split("把", 1)[1]
    if any(p in after for p in _BA_PREP):
        return False
    tail = after.strip()
    while tail and tail[-1] in "。！？!? ":
        tail = tail[:-1]
    return tail.endswith(_BA_TAIL)


class Recognizer:
    """偏误识别引擎（对齐 2.1 v0.3 schema）"""

    def __init__(self, client: LLMClient = None, knowledge_points_path: str = KNOWLEDGE_POINTS_PATH,
                 confidence_review_low: float = 0.5, confidence_high: float = 0.7):
        self.client = client or LLMClient()
        self.kps = load_knowledge_points(knowledge_points_path)
        self.portal = build_portal(self.kps)
        self.lexicon = load_lexicon()                      # 权威词表/字表（超纲真源）
        self.CONF_REVIEW_LOW = confidence_review_low   # 0.5
        self.CONF_HIGH = confidence_high               # 0.7

    @staticmethod
    def _is_false_de_adverb(error: dict) -> bool:
        """确定性护栏（3.5-CLN-016）：撤单"无'地'作状语被判缺'地'"的误报。
        若修正建议仅是在某可无地作状语的形容词后插入"地"（fragment 以该词开头），则属"地"可省，撤单。"""
        frag = error.get("fragment", "")
        corr = error.get("correction", "")
        for adv in _NO_DE_ADV:
            if frag.startswith(adv) and corr.startswith(adv + "地"):
                if corr[len(adv) + 1:] == frag[len(adv):]:
                    return True
        return False

    def recognize(self, text: str, level: int = 3, native_lang: str = "") -> dict:
        """识别偏误，返回 2.1 v0.3 结构（含 knowledge_point_id 命中校验 + beyond_level 权威判定）"""
        # 句子级超纲判定（确定性，规则层对照权威词表，不依赖 LLM）
        beyond = detect_beyond_level(text, level, self.lexicon)
        user_prompt = (
            f"【学习者】HSK{level}级，母语{native_lang or '未知'}。\n"
            f"\n请识别以下中文输出的偏误：\n\n{text}"
        )
        try:
            raw = self.client.chat_json(SYSTEM_PROMPT.format(knowledge_tree_portal=self.portal),
                                        user_prompt, temperature=0.0)
        except Exception as e:
            # 4.2 降级兜底：LLM 识别不可用 → 确定性规则回退（超纲词候选）
            return self._rule_fallback(text, beyond, reason=str(e))
        errors = raw.get("errors", [])

        # 置信度决策序列（2.1 §五）：先初分类，再标注命中校验
        confirmed, uncertain = [], []
        for e in errors:
            e = self._normalize(e)
            c1 = float(e.get("confidence", 0.0))
            t_conf = bool(e.get("type_confident", False))

            # 合并层：knowledge_point_id 命中校验（清单内才保留，清单外置空 → 待映射）
            kp_id = e.get("knowledge_point_id", "")
            e["kp_in_list"] = kp_id in self.kps
            if kp_id and kp_id not in self.kps:
                e["knowledge_point_id"] = ""
                e["uuid_mapped"] = False   # 进 2.4 pending_mapping

            # 决策序列：c1>=0.7 且 type_confident → 采纳；0.5<=c1<0.7 或 !confident → 待确认(触发复核)；c1<0.5 → 待确认
            if c1 >= self.CONF_HIGH and t_conf:
                uncertain_flag = False
                confirmed.append(e)
            elif 0.5 <= c1 < self.CONF_HIGH or not t_conf:
                uncertain_flag = True
                uncertain.append(e)
            else:
                uncertain_flag = True
                uncertain.append(e)
            e["uncertain"] = uncertain_flag

        # 确定性护栏（约束7 补洞，3.5-CLN-019）+ 状中"地"过度要求（3.5-CLN-016）：撤两类误报
        kept, dropped = [], []
        for _e in confirmed:
            if (_e["fragment"].find("把") != -1) and _is_legal_ba_disposal(text):
                dropped.append(_e)
            elif self._is_false_de_adverb(_e):
                dropped.append(_e)
            else:
                kept.append(_e)
        confirmed = kept

        # 0.22 方向1 · L1 迁移假设：仅对确认层偏误、且 native_lang 非 zh 时归因。
        # 纯确定性匹配（engine/transfer），status=candidate 绝不进图谱 confirmed 层；
        # 匹配异常静默降级为空（归因失败不影响识别主链）。
        try:
            hypotheses = transfer_match(confirmed, native_lang)
        except Exception:
            hypotheses = []

        return {"errors": confirmed, "uncertain": uncertain, "raw": raw,
                "kp_total": len(self.kps),
                "hypotheses": hypotheses,          # L1 迁移候选假设（0.22，只读不写图谱）
                "beyond_level": beyond,            # 权威词表判定结果 [{word,level}]
                "beyond_level_flag": bool(beyond),
                "dropped_fp": dropped}

    def _rule_fallback(self, text: str, beyond: list, reason: str = "") -> dict:
        """4.2 降级兜底：识别引擎不可用时的确定性规则回退。
        只产低置信 uncertain 候选（超纲词命中），绝不产 confirmed——
        宁漏勿错：规则层没有语法/搭配判断力，进图谱确认层会污染偏误数据。"""
        uncertain = [{
            "fragment": b.get("word", ""),
            "correction": "",
            "type": "词汇",
            "type_confident": False,
            "confidence": 0.3,
            "knowledge_point_id": "",
            "source": "rule_fallback",
        } for b in beyond]
        return {"errors": [], "uncertain": uncertain, "raw": {},
                "kp_total": len(self.kps),
                "hypotheses": [],                  # 降级路径无确认偏误，无迁移假设（0.22）
                "beyond_level": beyond, "beyond_level_flag": bool(beyond),
                "dropped_fp": [],
                "degraded": f"识别引擎不可用，已回退规则匹配（仅超纲词预检）: {reason}"}

    @staticmethod
    def _normalize(error: dict) -> dict:
        """兼容模型输入，补齐缺省键"""
        return {
            "fragment": error.get("fragment", ""),
            "correction": error.get("correction", ""),
            "type": error.get("type", ""),
            "type_confident": error.get("type_confident", True),
            "confidence": error.get("confidence", 0.0),
            "knowledge_point_id": error.get("knowledge_point_id", ""),
        }
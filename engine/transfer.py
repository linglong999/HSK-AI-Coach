# ============================================================
# engine/transfer.py
# 0.22 方向1 · L1 母语迁移归因（迁移带）
# 对识别出的偏误产出"疑似母语迁移"候选假设：
#   - 纯确定性匹配（规则表 + fragment/correction 签名），零 LLM 调用
#   - 假设 status 恒为 "candidate"，绝不进偏误图谱 confirmed 层（宁漏勿错）
#   - native_lang 为 zh/空、或无规则命中 → 返回 []（不追因）
# 规则表：datasets/transfer_rules_en.json（data/ 被 gitignore，规则是分发资产）
# ============================================================

import json
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(_PROJECT_ROOT, "datasets", "transfer_rules_en.json")

# 签名检查用字符集（仅匹配用，不是权威量词表）
_CLASSIFIERS = set("个本张件部台只条块杯瓶家位匹棵朵封辆间首歌道双对")
_NUMERALS = set("一二两三四五六七八九十百几半")
_PRONOUN_TAILS = set("我你他她它咱们这那谁")
_ASPECT_PARTICLES = ("了", "着", "过")
_WH_WORDS = ("什么时候", "为什么", "什么", "哪儿", "哪里", "怎么", "谁", "多少", "几")
_QUANTITY_WORDS = ("很多", "许多", "不少", "好多", "好几个")

# 语言别名归一（边界鲁棒：客户端/旧测试可能送 "英语"/"English"/"中文"）
_L1_ALIASES = {"english": "en", "英语": "en", "英文": "en",
               "chinese": "zh", "中文": "zh", "汉语": "zh", "汉语官话": "zh"}


def _norm_l1(native_lang) -> str:
    v = str(native_lang or "").strip().lower()
    return _L1_ALIASES.get(v, v)

_rules_cache = None


def load_rules(path: str = RULES_PATH) -> list:
    """加载迁移规则表（进程内缓存）。文件缺失/损坏 → 空表（不产假设，不阻断）。"""
    global _rules_cache
    if _rules_cache is not None:
        return _rules_cache
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        rules = data.get("rules", []) if isinstance(data, dict) else []
    except Exception:
        rules = []
    _rules_cache = [r for r in rules if r.get("rule_id")]
    return _rules_cache


def _strip_punct(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch not in "。，！？!?,. ")


# ---------- 六条规则各自的签名检查（fragment vs correction 确定性比对） ----------

def _sig_classifier(err: dict) -> bool:
    """量词缺失：修正 = 在数词后插入一个量词字（三苹果→三个苹果）；
    量词泛化：fragment 与 correction 恰差一字且 fragment 处为'个'（一个手机→一部手机）。"""
    frag, corr = _strip_punct(err.get("fragment", "")), _strip_punct(err.get("correction", ""))
    if not frag or not corr or frag == corr:
        return False
    # 插入型：corr 去掉一个量词字 == frag，且被插位置前一字是数词
    for i, ch in enumerate(corr):
        if ch in _CLASSIFIERS and i > 0 and corr[i - 1] in _NUMERALS and \
                corr[:i] + corr[i + 1:] == frag:
            return True
    # 替换型：等长且恰差一字，frag 处是'个'、corr 处是其他量词
    if len(frag) == len(corr):
        diffs = [(a, b) for a, b in zip(frag, corr) if a != b]
        if len(diffs) == 1 and diffs[0][0] == "个" and diffs[0][1] in _CLASSIFIERS:
            return True
    return False


def _sig_possessive_de(err: dict) -> bool:
    """'的'遗漏（P0.19 规则②收紧）：修正 = 恰好插入'的'，且片段以领属代词/指示代词开头
    （我朋友书→我朋友的书）。收紧理由：形容词+名词合法（漂亮衣服可加可不加'的'），
    单删'的'的形容词类不再误报；名词领属（朋友书）纯文本难与名词并列区分，宁漏勿错。"""
    frag, corr = _strip_punct(err.get("fragment", "")), _strip_punct(err.get("correction", ""))
    if not frag or not corr or "的" in frag or "的" not in corr:
        return False
    if not frag or frag[0] not in _PRONOUN_TAILS:  # 领属代词/指示代词开头才命中
        return False
    if corr.replace("的", "") != frag:
        return False
    i = corr.find("的")
    return i > 0 and i + 1 < len(corr)


def _sig_quantity_reorder(err: dict) -> bool:
    """数量词后置：修正 = 纯换序（同字符集重排，苹果很多→很多苹果），且含数量词。"""
    frag, corr = _strip_punct(err.get("fragment", "")), _strip_punct(err.get("correction", ""))
    if not frag or not corr or frag == corr or sorted(frag) != sorted(corr):
        return False
    return any(q in frag or q in corr for q in _QUANTITY_WORDS)


def _sig_adj_predicate(err: dict) -> bool:
    """形容词谓语两形态：
    A 系动词冗余：frag 含'是'、corr 不含，且去'是'后与去'很'后的 corr 重合（我是高兴→我很高兴）；
    B 裸形容词：corr 恰多一个'很'（她高兴→她很高兴）。"""
    frag, corr = _strip_punct(err.get("fragment", "")), _strip_punct(err.get("correction", ""))
    if not frag or not corr:
        return False
    if "是" in frag and "是" not in corr:
        a = frag.replace("是", "")
        b = corr.replace("很", "")
        if a and (a in b or b in a):
            return True
    return "很" in corr and "很" not in frag and corr.replace("很", "") == frag


def _sig_aspect_particle(err: dict) -> bool:
    """体标记增删：修正与原片段恰差一个'了/着/过'（我吃→我吃了；昨天我去学校→昨天我去了学校；
    他在看书了→他在看书）。要求"体助词是唯一差异"——修正同时动了语序的不算（防把字句假阳性）。"""
    frag, corr = _strip_punct(err.get("fragment", "")), _strip_punct(err.get("correction", ""))
    if not frag or not corr or frag == corr or abs(len(frag) - len(corr)) != 1:
        return False
    # 增：corr 去掉一个体助词 == frag
    for i, ch in enumerate(corr):
        if ch in _ASPECT_PARTICLES and corr[:i] + corr[i + 1:] == frag:
            return True
    # 删：frag 去掉一个体助词 == corr
    for i, ch in enumerate(frag):
        if ch in _ASPECT_PARTICLES and frag[:i] + frag[i + 1:] == corr:
            return True
    return False


def _sig_wh_fronting(err: dict) -> bool:
    """疑问词前置：frag 以疑问词开头，且修正把该疑问词移离句首（什么你要→你要什么）。"""
    frag, corr = _strip_punct(err.get("fragment", "")), _strip_punct(err.get("correction", ""))
    if not frag or not corr:
        return False
    return any(frag.startswith(wh) and wh in corr and not corr.startswith(wh)
               for wh in _WH_WORDS)


# 处置动词常见尾部标记（把字句回避检测：frag 缺'把'、corr 引入'把'提取宾语）
_BA_TAIL_HINTS = ("桌子", "床上", "椅子", "书包", "口袋", "地方", "手里", "车", "房间",
                  "前面", "上面", "中间")  # 处所/位置宾语常配合处置义"把+宾语+放在+处所"


def _sig_ba_avoidance(err: dict) -> bool:
    """把字句回避（P0.19 规则⑦，补迁移规则）：英语无把字句、以 SVO 语序表达处置义，
    学习者回避'把'、宾语留在动词后。判据（宁漏勿错，需三重条件 同时满足）：
      ① corr 引入'把'、frag 不含'把'（把+宾语 属新增处置框架）；
      ② 同字符集换序：frag 与 corr 去掉'把'后字符可重排一致（语序调整而非增删词）；
      ③ 含处置/放置动词 + 处所宾语（如'书在桌子上'），排除不含'把'的一般换序误报。
    局限（触发条件标注）：transfer_match 只对 confirmed 层匹配，而'回避'特征句面无错——
    纯回避（句面合法、只是没用把字句）静态规则捕获不到，本签名只命中'回避+其他偏误共存'场景；
    场景驱动的真回避检测进 backlog。"""
    frag, corr = _strip_punct(err.get("fragment", "")), _strip_punct(err.get("correction", ""))
    if not frag or not corr or "把" not in corr or "把" in frag:
        return False
    # ② 同字符集换序（去标点后）
    if sorted(frag) != sorted(corr.replace("把", "")):
        return False
    # ① ③ 引入'把'且含放置动词 + 处所宾语特征（本签名锚定的处理义语境）
    has_bind = "放" in frag or "摆" in frag or "挂" in frag or "放" in corr.split("把")[1]
    has_place = any(h in frag for h in _BA_TAIL_HINTS)
    # 排除：corr 用了'把'是量词/把持（把门/把车开走）而 frag 无对应处所宾语
    if not (has_bind and has_place):
        return False
    return True


_SIGNATURES = {
    "en-classifier-missing": _sig_classifier,
    "en-possessive-de": _sig_possessive_de,
    "en-quantity-adverb-postposed": _sig_quantity_reorder,
    "en-adj-predicate": _sig_adj_predicate,
    "en-aspect-particle": _sig_aspect_particle,
    "en-wh-fronting": _sig_wh_fronting,
    "en-ba-avoidance": _sig_ba_avoidance,
}


def match_one(error: dict, native_lang: str) -> dict:
    """对单个偏误产出迁移假设；无命中返回 None。
    三层判定（宁漏勿错）：
      ① l1 严格相等 —— 规则表按母语分文件，en 规则只对 en 学习者生效
         （ko 学习者套 en 规则 = 误诊，宁可不给归因）
      ② 锚点命中 —— knowledge_point_id ∈ kp_anchors 或 type ∈ types
      ③ 签名命中 —— fragment/correction 满足该规则的确定性签名
    三者同时满足才产假设；每条偏误至多一条（按规则表顺序取首个命中）。"""
    if not native_lang or _norm_l1(native_lang) == "zh":
        return None
    if not isinstance(error, dict):
        return None
    l1 = _norm_l1(native_lang)
    kp = str(error.get("knowledge_point_id", "") or "")
    etype = str(error.get("type", "") or "")
    for rule in load_rules():
        if rule.get("l1", "en") != l1:
            continue
        kp_hit = bool(kp) and kp in rule.get("kp_anchors", [])
        type_hit = etype in rule.get("types", [])
        if not (kp_hit or type_hit):
            continue
        sig = _SIGNATURES.get(rule["rule_id"])
        if sig and sig(error):
            return {
                "rule_id": rule["rule_id"],
                "l1": rule.get("l1", "en"),
                "conf": float(rule.get("conf", 0.4)),
                "status": "candidate",
                "fragment": error.get("fragment", ""),
                "l1_anchor": rule.get("l1_anchor", ""),
                "zh_signature": rule.get("zh_signature", ""),
                "correction": error.get("correction", ""),
            }
    return None


def match(errors: list, native_lang: str) -> list:
    """对一批已确认偏误产出迁移假设列表（识别结果 hypotheses[] 的唯一来源）。
    只对确认层偏误做归因（uncertain 本身未站稳，叠加归因会放大不确定性）。"""
    if not native_lang or _norm_l1(native_lang) == "zh":
        return []
    return [h for h in (match_one(e, native_lang) for e in errors or []) if h]

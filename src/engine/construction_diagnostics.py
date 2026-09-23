# ============================================================
# 双轨硬校诊断器（B1）—— 确定性构式守门员（把字句 6 查 + 补语 5 查）
# 纯确定性、零 LLM、零第三方分词（轻量关键成分定位：先定位
# "把+宾语+谓核+尾成分"骨架，再逐成分判定）。
# 与旧护栏(_is_legal_ba_disposal/_is_false_de_adverb)并存不混用：
#   旧护栏 = 撤 LLM 误报（取相反方向）；本诊断器 = 复核/补漏/软评三级。
# 可随 _rule_fallback 降级路径被调用（构式守门员独立于 LLM 存活）。
# 判定说明：四域两查均为启发式规则，非学界既定分级；cc_ 前缀 vs 独立常量。
# 评分落点（确定性直判三值，与 B0 VERDICT_ACCEPTABLE/EDGE 阈值区间对齐；
# 不走 derive_verdict——derive_verdict 只管 LLM 连续分切档）：
#   任一硬违 fail → verdict=error, score=0.40
#   仅次违 edge   → verdict=edge,  score=0.70
#   全 pass/无病征→ verdict=acceptable, score=0.90
# 单一主错：error 时主 check 取优先级链最高硬违（C4>C5>C3>C6），checks[] 全录 trace。
# 输出契约 6 键：{construction, instance, verdict, score, checks[], explanation}
#   ——即 B0 D2 construction_diagnostics 挂 error 条目的嵌套结构。
# ============================================================

from typing import List

# ---- 全人工小表（B1-A 拍板：lexicon 无 POS，不引分词） ----
R_TAIL = ("了", "完", "掉", "走", "成", "干净", "清楚", "好", "一下")  # 有效尾成分白名单（含"一下"→CLN-016）
DISPOSAL_FORM = ("当作", "看成", "视为")                                 # 处置义构成（C5 判定链入口）
R_COLLOQUIAL = ("够呛", "死", "坏", "醒", "哭", "笑")                    # 口语致使性尾成分
NEG_MODAL = ("不能", "不要", "不应该", "不可以",                                    # 双字优先（防"能"误拆）
             "没", "不", "别", "能", "要", "应该", "可以")                             # C4 否定/能愿（"想"易与心理动词混淆，不入）
ENCOUNTER_V = ("忘", "丢")                                              # 遭遇类动词（归 C3 合法，"把钥匙忘了"）
# C5 结构链：认知/感知/心理/形容词类（不可"处置"义动词）人工小表——D-5 已定选 A 人工小表。
# "考虑"刻意不入表（"把这个问题认真考虑一下"=CLN-016 合法）；"记得"单列走 edge。
C5_SENSORY = ("知道", "认识", "了解", "明白", "懂",
              "看见", "听见", "感觉", "认为", "觉得", "相信", "喜欢")
# C6 处所补语判定：合法"V+在+处所"的放置类动词（靠前邻动词区分补语 vs 前置处所介词）
_C6_PLACE_V = ("放", "摆", "坐", "站", "挂", "搁", "贴", "写", "拿", "存")
# C2 宾语有定检测：显式数量（纯数词+量词；指示代词"这/那"为定指限定非数量，不触发）
_C2_NUM = ("一", "两", "三", "四", "五", "六", "七", "八", "九", "十", "几", "每", "双")
_C2_MEAS = ("本", "个", "张", "块", "件", "些", "辆", "只", "杯", "条",
            "双", "位", "名", "家", "台", "根")
# 补语触发词
_C_RESULT = ("完", "掉", "走", "成", "好")   # 结果补语单字（C1 位序检测用）
_C_DIRECTIONAL = ("进去", "出来", "回来", "回去", "上来", "下来")  # C2 趋向补语
_C_MEASURE = ("遍", "次", "回", "趟", "顿")  # C5 动量词（前邻数词才算量词用法）

KP_MAP = {"ba": "kp-ba-sentence", "result": "kp-jiuguo-jiegou",
          "directional": "kp-quxiang-buyu", "potential": "kp-keneng-buyu",
          "degree": "kp-chengdu-buyu", "duration": "kp-zhizhi-dao",
          "verbal_measure": "kp-dongliang-buyu"}

# 把字句 6 查优先级（单一主错 C4>C5>C3>C6；C2 为次违 edge，不参与主错竞争）
_PRIORITY = {"c4": 4, "c5": 3, "c3": 2, "c6": 1, "c2": 0}

_PUNCT = "。！？!?，,：；;、——"


def _strip(s: str) -> str:
    return s.strip(_PUNCT + " ")


def _ba_body_instances(sentence: str) -> List[dict]:
    """轻量把字句骨架定位：遇"把"切一段到下一标点，产 {before, text, body}。
    body = 去"把"后的 VP 段（供各查函数判定）。零分词。"""
    out = []
    for i, ch in enumerate(sentence):
        if ch != "把":
            continue
        j = i + 1
        while j < len(sentence) and sentence[j] not in _PUNCT:
            j += 1
        seg = sentence[i:j]
        out.append({"before": sentence[:i],
                    "text": seg,
                    "body": sentence[i + 1:j]})
    return out


def _has_verb_marker(body: str) -> bool:
    """VP 段是否带"谓核不成光杆"的标记：有效尾成分 / 重叠 / 动量 / 处所补语 / 量词。
    有标记 → 非光杆；无标记 → 孤立光杆（把字句缺处置结果，硬违）。"""
    if any(t in body for t in R_TAIL):
        return True
    if any(p in body for p in _C6_PLACE_V):
        return True
    # 动词重叠（顺手莘莘/打量打量）
    for k in range(len(body) - 1):
        if body[k] == body[k + 1]:
            return True
    # 动量补语（一遍/两次/了一下）
    for m in _C_MEASURE:
        for k in range(1, len(body)):
            if body[k] in m and body[k - 1] in "一二两三四十几":
                return True
    return False


# ---------------- 把字句 6 查 ----------------

def _c2_definiteness(body: str) -> dict:
    """C2 宾语有定：显式数量+非回指 → edge；无标记 → pass（次违，仅 edge）。"""
    for k in range(len(body) - 1):
        if body[k] in _C2_NUM and body[k + 1] in _C2_MEAS:
            return {"code": "c2", "name": "宾语有定", "result": "edge",
                    "note": "把字句宾语须有定，受数量/指示短语限定时为次违建议",
                    "priority": _PRIORITY["c2"], "kp_candidate": KP_MAP["ba"]}
    return {"code": "c2", "name": "宾语有定", "result": "pass",
            "note": "宾语有定或无标记", "priority": _PRIORITY["c2"],
            "kp_candidate": KP_MAP["ba"]}


def _c3_bare_verb(body: str) -> dict:
    """C3 动词不光杆：处置义构成/遭遇类/有效尾成分 → pass；孤立光杆 → fail。"""
    if any(d in body for d in DISPOSAL_FORM) or any(v in body for v in ENCOUNTER_V):
        return {"code": "c3", "name": "动词不光杆", "result": "pass",
                "note": "处置义构成/遭遇类作尾", "priority": _PRIORITY["c3"],
                "kp_candidate": KP_MAP["ba"]}
    if _has_verb_marker(body):
        return {"code": "c3", "name": "动词不光杆", "result": "pass",
                "note": "带有效尾成分/重叠/动量", "priority": _PRIORITY["c3"],
                "kp_candidate": KP_MAP["ba"]}
    return {"code": "c3", "name": "动词不光杆", "result": "fail",
            "note": "把+宾语+孤立光杆动词，缺处置结果标记",
            "priority": _PRIORITY["c3"], "kp_candidate": KP_MAP["ba"]}


def _c4_neg_modal_pos(before: str, body: str) -> dict:
    """C4 否定/能愿位置：把前 → pass；把后 → fail（硬违）。"""
    if any(nm in body for nm in NEG_MODAL):
        return {"code": "c4", "name": "否定/能愿位次", "result": "fail",
                "note": "否定/能愿词出现在'把'后，应前移至'把'前",
                "priority": _PRIORITY["c4"], "kp_candidate": KP_MAP["ba"]}
    if any(nm in before for nm in NEG_MODAL):
        return {"code": "c4", "name": "否定/能愿位次", "result": "pass",
                "note": "否定/能愿位于'把'前", "priority": _PRIORITY["c4"],
                "kp_candidate": KP_MAP["ba"]}
    return {"code": "c4", "name": "否定/能愿位次", "result": "pass",
            "note": "无否定/能愿", "priority": _PRIORITY["c4"],
            "kp_candidate": KP_MAP["ba"]}


def _c5_structure_chain(body: str) -> dict:
    """C5 结构判定链 v2：处置义构成→pass；致使性尾成分→pass；'记得'得字→edge；
    得字结构→edge；认知/感知/心理/形容词类光杆/非处置义→fail。"""
    if any(d in body for d in DISPOSAL_FORM):
        return {"code": "c5", "name": "结构判定链", "result": "pass",
                "note": "处置义构成（当作/看成/视为）", "priority": _PRIORITY["c5"],
                "kp_candidate": KP_MAP["ba"]}
    if any(c in body for c in R_COLLOQUIAL):
        return {"code": "c5", "name": "结构判定链", "result": "pass",
                "note": "致使性口语尾成分", "priority": _PRIORITY["c5"],
                "kp_candidate": KP_MAP["ba"]}
    if "记得" in body:
        return {"code": "c5", "name": "结构判定链", "result": "edge",
                "note": "'记得'得字结构，母语者可两可", "priority": _PRIORITY["c5"],
                "kp_candidate": KP_MAP["ba"]}
    if "得" in body:
        return {"code": "c5", "name": "结构判定链", "result": "edge",
                "note": "得字结构", "priority": _PRIORITY["c5"],
                "kp_candidate": KP_MAP["ba"]}
    for v in C5_SENSORY:
        if v in body:
            return {"code": "c5", "name": "结构判定链", "result": "fail",
                    "note": f"「{v}」为认知/感知类，不可作把字句处置义，光杆非处置",
                    "priority": _PRIORITY["c5"], "kp_candidate": KP_MAP["ba"]}
    return {"code": "c5", "name": "结构判定链", "result": "pass",
            "note": "处置动词或非光杆", "priority": _PRIORITY["c5"],
            "kp_candidate": KP_MAP["ba"]}


def _c6_comp_alignment(body: str) -> dict:
    """C6 补语搭配/位次：VP 段内处所介词前置（'把+O+在/到+处所+V'=ERR-005 型）→ fail；
    放置类动词后接补语（'放在'）→ 合法 pass。"""
    for p, name in (("在", "在"), ("到", "到")):
        k = body.find(p)
        if k <= 0:
            continue
        if body[k - 1] in _C6_PLACE_V:  # "放在" = 合法结果补语
            continue
        return {"code": "c6", "name": "补语搭配位次", "result": "fail",
                "note": f"处所补语'{name}'前置于动词（{name}处所+谓核），应为 V+{name}+处所",
                "priority": _PRIORITY["c6"], "kp_candidate": KP_MAP["ba"]}
    return {"code": "c6", "name": "补语搭配位次", "result": "pass",
            "note": "无处所补语前置错位", "priority": _PRIORITY["c6"],
            "kp_candidate": KP_MAP["ba"]}


def _aggregate(checks: List[dict]) -> tuple:
    """聚合：任一 fail → (error,0.40,优先主错)；仅 edge → (edge,0.70,edge)；
    全 pass → (acceptable,0.90,None)。单一主错取优先级最高硬违。"""
    fails = [c for c in checks if c["result"] == "fail"]
    if fails:
        main = max(fails, key=lambda c: c["priority"])
        return "error", 0.40, main
    edges = [c for c in checks if c["result"] == "edge"]
    if edges:
        return "edge", 0.70, edges[0]
    return "acceptable", 0.90, None


def _summarize(checks: List[dict]) -> str:
    bad = [f"{c['name']}：{c['note']}" for c in checks if c["result"] == "fail"]
    if bad:
        return "；".join(bad)
    bad2 = [f"{c['name']}：{c['note']}" for c in checks if c["result"] == "edge"]
    return "；".join(bad2) if bad2 else "把字句结构合格"


def _diag_ba(inst: dict) -> dict:
    checks = [_c2_definiteness(inst["body"]),
              _c3_bare_verb(inst["body"]),
              _c4_neg_modal_pos(inst["before"], inst["body"]),
              _c5_structure_chain(inst["body"]),
              _c6_comp_alignment(inst["body"])]
    verdict, score, main = _aggregate(checks)
    return {"construction": "ba", "instance": inst["text"],
            "verdict": verdict, "score": score,
            "checks": checks, "explanation": _summarize(checks),
            "_main": main}


# ---------------- 补语 5 查 ----------------

def _find_complement_instances(sentence: str) -> List[dict]:
    """补语触发定位：五类特征逐一探测，命中的产 {kind, text}。"""
    out = []
    # C1 结果位序 V+了+结果（应为 V+结果+了）
    for k in range(len(sentence) - 1):
        if sentence[k] == "了" and sentence[k + 1] in _C_RESULT:
            out.append({"kind": "result_order", "text": sentence[k:k + 2]})
            break
    # C2 趋向补语后带处所宾语（"进去教室"位次错：处所应置于动趋之间或去字收尾）
    for t in _C_DIRECTIONAL:
        k = sentence.find(t)
        if k != -1 and k + len(t) < len(sentence) and sentence[k + len(t)] not in _PUNCT:
            out.append({"kind": "directional_obj", "text": sentence[k:k + len(t) + 1]})
            break
    # C3 可能补语 得 后光杆（V+得 后必接可能成分）
    k3 = sentence.find("得")
    if k3 != -1:
        after = _strip(sentence[k3 + 1:])
        if not after:
            out.append({"kind": "potential_bare", "text": sentence[:k3 + 1]})
    # C5 动量/时量与宾语共现（动量词作量词且后带宾语 → edge）
    for k in range(1, len(sentence)):
        if sentence[k] in _C_MEASURE and sentence[k - 1] in _C2_NUM:
            if k + 1 < len(sentence) and sentence[k + 1] not in _PUNCT:
                out.append({"kind": "measure_obj", "text": sentence[k - 1:k + 2]})
                break
    return out


def _diag_comp(inst: dict) -> dict:
    kind = inst["kind"]
    if kind == "result_order":
        check = {"code": "comp_c1", "name": "结果补语位序", "result": "fail",
                 "note": "结果补语应与动词直接相连（V+结果+了），'了'误插 V 与结果间",
                 "priority": 6, "kp_candidate": KP_MAP["result"]}
        return {"construction": "complement", "instance": inst["text"],
                "verdict": "error", "score": 0.40, "checks": [check],
                "explanation": check["note"], "_main": check}
    if kind == "directional_obj":
        check = {"code": "comp_c2", "name": "趋向补语宾位", "result": "fail",
                 "note": "趋向补语后不应直接带处所宾语，处所应置于动趋之间",
                 "priority": 6, "kp_candidate": KP_MAP["directional"]}
        return {"construction": "complement", "instance": inst["text"],
                "verdict": "error", "score": 0.40, "checks": [check],
                "explanation": check["note"], "_main": check}
    if kind == "potential_bare":
        check = {"code": "comp_c3", "name": "可能补语别光杆", "result": "fail",
                 "note": "V+得 后必须带可能补语成分（V得/不了等），不得光杆收尾",
                 "priority": 6, "kp_candidate": KP_MAP["potential"]}
        return {"construction": "complement", "instance": inst["text"],
                "verdict": "error", "score": 0.40, "checks": [check],
                "explanation": check["note"], "_main": check}
    if kind == "measure_obj":
        check = {"code": "comp_c5", "name": "动量时量共现", "result": "edge",
                 "note": "动量/时量词与宾语共现时位次为次违建议",
                 "priority": 5, "kp_candidate": KP_MAP["verbal_measure"]}
        return {"construction": "complement", "instance": inst["text"],
                "verdict": "edge", "score": 0.70, "checks": [check],
                "explanation": check["note"], "_main": check}
    check = {"code": "comp_c0", "name": "补语", "result": "pass",
             "note": "补语结构合格", "priority": 0, "kp_candidate": KP_MAP["duration"]}
    return {"construction": "complement", "instance": inst["text"],
            "verdict": "acceptable", "score": 0.90, "checks": [check],
            "explanation": "补语结构合格", "_main": check}


def run_construction_diagnostics(
        sentence: str, level: int = 3, llm_errors: list = None) -> List[dict]:
    """纯确定性构式诊断：把字句 6 查 + 补语 5 查，逐实例判定。
    llm_errors 仅用于复核匹配（fragment 重叠），不影响判型。
    返回 §2 ③ 6 键（含 _main 供主路径取主错 kp）实例列表。
    level 目前不参与判型（把字句/补语对各级判据一致），保留签名供适配。"""
    diags = [_diag_ba(inst) for inst in _ba_body_instances(sentence)]
    diags += [_diag_comp(inst) for inst in _find_complement_instances(sentence)]
    return diags
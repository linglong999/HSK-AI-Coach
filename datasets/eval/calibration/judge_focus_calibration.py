# -*- coding: utf-8 -*-
# ============================================================
# B6 校准 · judge「单句 focus 维」评分路径（judge_focus_calibration.py）
# ------------------------------------------------------------------
# 背景：第一轮对拍 kappa=0.4552 未达标，根因=judge 输入形态错误——
#   judge_calibration.py 用 _minimal_case 把单句文本包成生产 case（detected=[]、
#   trace={}）喂生产 _l2_llm_judge，judge 按"errors 为空 / 单句独白无推进"系统性压分
#   （low 均1 vs 人工1.07、mid 均1.05 vs 2.92、high 均3.10 vs 4.83）。
# 本脚本新增独立评分口径（与人工对齐、kappa 才可比）：
#   · 每条只评该条的 focus_dim 一维（1–5），不复用生产 transcript 口径；
#   · prompt 明示"这是独立教学回复文本，非完整对话、无 errors/context"，按
#     rubric.md v2 该维锚打分；
#   · 不注入 errors/trace 语境（消除"空对话/errors 为空"压分偏差）。
# 产出 judge_verdicts_focus.json（不覆盖旧 judge_verdicts.json，留档对照）。
# 不做 gold_judge_hint 回填刷 kappa（那是真实分歧路径；此处用=拟合测试集）。
# 验收：low 均分≈1、mid≈2.5–3.5、high≈4–5；judge 理由不得再出现"空对话/errors 为空"。
# ============================================================
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../eval/calibration
_EVAL = os.path.dirname(_HERE)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_EVAL))
for _p in (_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, "src"), _EVAL,
           os.path.join(_EVAL, "tutor_quality"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from engine.llm.client import LLMClient  # noqa: E402

_CASES = os.path.join(_HERE, "calibration_cases.json")
_RUBRIC = os.path.join(_EVAL, "tutor_quality", "rubric.md")
_OUT = os.path.join(_HERE, "judge_verdicts_focus.json")
CATEGORIES = (1, 2, 3, 4, 5)

# 与 build_user_sheet.py 的 DIM_NAME 保持一致（打分锚口径统一）
DIM_NAME = {
    1: "偏误定位准确", 2: "讲解四段完整", 3: "复述验证严格", 4: "介入时机合理",
    5: "语言分层守约", 6: "归因谨慎", 7: "任务真实性", 8: "角色代入",
    9: "非命令性", 10: "对话推进感",
}

# 每维 1/3/5 描述性档锚（v2 锚定版）。来源=rubric 判定要点 + 校准集设计意图，
# 用"描述"不给具体样本文本（避免泄漏答案）；显式把 3=中间档写实，纠正 judge 双峰化。
# 权威来源=rubric.md §七（生产 judge 整篇注入即继承）；本表与 rubric §七 保持同步。
ANCHORS = {
    1: ("偏误定位准确", "只说'不对/得改'，不给具体错点、类型或正确说法，定位缺失",
        "点出大致方向但未精确（如'是'给'的位置问题'），未给明确改法或错因不完整",
        "精准指出真错点（对齐具体词/结构）并给正确说法，不误报不教错"),
    2: ("讲解四段完整", "四段全缺：仅改法指令或否定，无'为什么错/正确/对比/验证'任一",
        "讲清'为什么错+正确说法'但缺对比或验证，四段不全",
        "为什么错→正确说法→对比→验证 四段齐全"),
    3: ("复述验证严格", "完全不引导复述/验证，讲了错因就结束",
        "有复述引导但仅单次/泛问，未体现'连续2次不过降难度、拒绝假懂'",
        "明确要求复述+预设连续2次不过降难度+拒绝'假懂'"),
    4: ("介入时机合理", "无难度/求助依据强行打断，明显过度干预流畅表达",
        "有介入意识但未明确判断难度或流利度信号，介入偏机械或信息不足",
        "识别流畅表达主动不打断，或介入严格匹配难度与求助信号"),
    5: ("语言分层守约", "全英文无中文内容载体，或全中文无英文脚手架（且缺分层）",
        "有英文脚手架+中文内容载体，但某一侧占比失衡、分层不彻底",
        "英文脚手架+中文内容载体均衡分层、各司其职"),
    6: ("归因谨慎", "把母语迁移假设当事实/绝对断言（'一定因为英文'），污染图谱",
        "涉及或回应归因，但未明确标注'可能/假设'，也未干净地把重点拉回正确形式",
        "明确标注'可能/仅假设'并主动把重点拉回正确形式，不污染图谱"),
    7: ("任务真实性", "脱离真实交际任务的机械练习（造句/翻译/背诵），无任务目标",
        "有真实交际任务但流于形式（指令式带过，未展开可交际内容）",
        "任务真实可交际、围绕场景（点餐/问路/购物/就医）目标清晰"),
    8: ("角色代入", "旁观者旁白/冷读/日志口吻，完全无对话角色身份",
        "偶有角色感或仅作角色设定，未以角色身份自然承接对话",
        "全程以对话角色/场景身份自然承接，语气贴合场景"),
    9: ("非命令性", "命令链（'跟我读''现在输出''必须''马上'），无选择感",
        "半引导半指令：给了引导话术但仍以指令收尾",
        "选择/邀请式引导（'要不要…还是…''你来定'），尊重学习者自主"),
    10: ("对话推进感", "有来无回/单句循环无承接（纯'再来一次'原地打转）",
        "有来有回但打转/只推进半格，承接了但未真正螺旋前进",
        "回应承接+明确下一步，螺旋推进、收尾自然"),
}


class _CompleteAdapter:
    """judge.py 契约 `.complete(prompt, temperature, model) -> str`。model 为空走 settings 全局模型。"""

    def __init__(self, lc):
        self._lc = lc

    def complete(self, prompt, temperature=0, model=""):
        config = {"model": model} if model else None
        return self._lc.chat([{"role": "user", "content": prompt}],
                             temperature=temperature, config=config)


def _load_rubric():
    with open(_RUBRIC, encoding="utf-8") as f:
        return f.read()


def focus_prompt(c, rubric):
    """构造 focus 维评分 prompt：独立文本 + 只评 focus_dim + rubric 该维锚 + 本维 1/3/5 描述性档锚。"""
    fd = c["dim"]
    name = DIM_NAME.get(fd, "?")
    anchor = ANCHORS.get(fd)
    anchor_block = ""
    if anchor:
        _, a1, a3, a5 = anchor
        anchor_block = (
            f"\n下面给出「{name}」这一维的 1/3/5 档锚，请据此用完整 1–5 标尺打分：\n"
            f"  1 分：{a1}\n"
            f"  3 分：{a3}\n"
            f"  5 分：{a5}\n"
            "3 分是真实存在的中间档——文本若达到『部分做到/流于形式/半引导半指令』即在 3 分附近，"
            "**不要因略有不足就把中等文本一律压到 1–2，也不要因尚可就美化到 5**。\n"
        )
    return (
        "你是一位 HSK 中文教学专家评卷人。下面给出一段**独立的教学回复文本**——它是"
        "一段单句或单段教学话语，**不是完整师生对话：没有学习者输入、没有纠错错误清单、"
        "没有对话上下文**。请只评价其中一个维度并打 1–5 分。\n\n"
        f"评分依据 rubric v2 全文（注意定位到「被测维度」那一维的判据与锚）：\n"
        f"{rubric}\n\n"
        f"被测维度：{fd} · {name}\n\n"
        f"文本：\n{c['text']}\n\n"
        f"请只对上述被测维度（{name}）打 1–5 分（1=完全没做到，5=做得很好）。"
        f"{anchor_block}"
        "严格只输出一个 JSON 对象（不要任何其他文字）：\n"
        '{"focus_score": <1-5>, "reason": "一句话评分理由"}'
    )


def parse_focus(raw):
    """解析 LLM 输出 → (focus_score int|None, reason, pending)。非法 schema → pending=True。"""
    import re
    txt = (raw or "").strip()

    def _fill(obj, wholeraw):
        fs = obj.get("focus_score")
        if isinstance(fs, bool) or not isinstance(fs, int) or fs not in CATEGORIES:
            return None
        return fs

    try:
        obj = json.loads(txt)
        fs = _fill(obj, txt)
        if fs is not None:
            return fs, obj.get("reason", ""), False
    except json.JSONDecodeError:
        pass
    # LLM 偶发在 reason 里嵌未转义引号 → json 非法；正则兜底取 focus_score 数字
    m = re.search(r'"focus_score"\s*:\s*([1-5])', txt)
    if m:
        return int(m.group(1)), "", False
    s, e = txt.find("{"), txt.rfind("}")
    if s != -1 and e > s:
        frag = txt[s:e + 1]
        try:
            obj = json.loads(frag)
            fs = _fill(obj, frag)
            if fs is not None:
                return fs, obj.get("reason", ""), False
        except json.JSONDecodeError:
            pass
    return None, (txt[:120] or "(空输出)"), True


def judge_focus_case(c, client, judge_model=""):
    """单条 focus 维评分。失败/非法 → pending（不静默 fail）。"""
    prompt = focus_prompt(c, _load_rubric())
    try:
        raw = client.complete(prompt, temperature=0,
                              model=judge_model or None)
    except Exception as e:  # noqa: BLE001
        return {"focus_dim": c["dim"], "focus_score": None, "reason": f"LLM 调用失败: {e}",
                "pending": True}
    fs, reason, pending = parse_focus(raw)
    return {"focus_dim": c["dim"], "focus_score": fs, "reason": reason, "pending": pending}


def smoke_select(cases, n=10):
    """smoke 选样：按 quality 三档均衡取 n 条，桶内按 dim 排序保证维度分散（高/中/低搭配）。"""
    buckets = {"high": [], "mid": [], "low": []}
    for c in cases:
        buckets.setdefault(c["quality"], []).append(c)
    buckets = {q: sorted(lst, key=lambda c: c["dim"]) for q, lst in buckets.items()}
    total = len(cases)
    out, weights = [], {"high": 3, "mid": 4, "low": 3}  # 默认 10 条比例 3/4/3
    scale = n / 10
    for q, take_w in weights.items():
        take = max(1, round(take_w * scale))
        # 桶内隔位取，覆盖不同 dim
        g = buckets[q]
        step = max(1, len(g) // take)
        chosen = [g[i] for i in range(0, len(g), step)][:take]
        out.extend(chosen)
    out.sort(key=lambda c: c["id"])
    return out[:n]


def main():
    ap = argparse.ArgumentParser(description="单句 focus 维 judge 评分（新口径）")
    ap.add_argument("--smoke", type=int, default=0,
                    help="只跑 N 条（高/中/低搭配，取分层选样）供人工审 reason")
    ap.add_argument("--model", default="", help="judge 模型 id（默认 settings 全局）")
    args = ap.parse_args()

    cases = json.load(open(_CASES, encoding="utf-8"))["cases"]
    if args.smoke:
        sel = smoke_select(cases, args.smoke)
        tag = "smoke"
    else:
        sel = cases
        tag = "full"

    adapter = _CompleteAdapter(LLMClient())
    out = {}
    for i, c in enumerate(sel, 1):
        res = judge_focus_case(c, adapter, args.model)
        out[c["id"]] = {"focus_score": res["focus_score"], "focus_dim": c["dim"],
                        "quality": c["quality"], "reason": res["reason"],
                        "pending": res["pending"]}
        print(f"[{i}/{len(sel)}] {c['id']} dim{c['dim']} {c['quality']} "
              f"focus={res['focus_score']} pending={res['pending']}")
        if res["pending"]:
            print("   PENDING:", res["reason"])

    # smoke 也落盘独立文件，便于你核对；全量写正式 _OUT
    path = _OUT if tag == "full" else _OUT.replace(".json", f"_{tag}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(out)} -> {path}")

    # smoke 验收概览（quality 分档均分）
    if args.smoke:
        from collections import defaultdict
        mean = defaultdict(list)
        for cid, r in out.items():
            if r["focus_score"] is not None:
                mean[r["quality"]].append(r["focus_score"])
        for q in ("high", "mid", "low"):
            a = mean[q]
            print(f"   [{q}] n={len(a)} focus均值={sum(a)/len(a):.2f}" if a else f"   [{q}] 无")


if __name__ == "__main__":
    main()
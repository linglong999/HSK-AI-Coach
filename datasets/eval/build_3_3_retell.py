# -*- coding: utf-8 -*-
"""3.3 复述验证黄金集合并器（v0.2）
方向一(构造覆盖变体集) × 方向二(MuCGEC 真实学习者错误句→评测样本) 合并。
方向二转化逻辑：
  - 取真实学习者错误句，提取"该点讲解要点" key_points（手工标注，仿方向一）
  - 为每句构造四类复述变体：pass(用修正后要点) / partial(半对) / fail(维持错误) / hollow(流利空洞)
  - truth/pass与fail 由"是否体现修正要点"规则可判；partial/hollow 语义需二语仲裁 → need_arbitration
用法: python datasets/eval/build_3_3_retell.py
"""
import json
import os
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(BASE, "golden_v1_4.json")
OUT = os.path.join(BASE, "retell_verification.json")

# ============================================================
# 方向一模板（构造覆盖变体集，保留）
# 用 corrected + 学习者复述的相关性构造。
# evid: 每条变体"体现修正要点"的证据片段(pass应命中, fail不应命中), 供校验
# ============================================================
TEMPLATES = [
    dict(
        kp_id="HSK1-ERR-001", explanation="量词误用：猫用‘只’不用‘个’。正确为‘我有一只猫’。",
        key_points=[
            {"id": 1, "text": "要用名量词‘只’来数动物（猫）"},
            {"id": 2, "text": "量词‘只’放在数词‘一’和名词‘猫’之间"},
        ],
        variants=[
            ("pass", "我有一只猫。", "一只"),
            ("fail", "我有一个猫。", "个"),
            ("partial", "我猫有一只。", "只"),  # 说对"只"但语序仍错位(次要点漏)
            ("hollow", "猫这个动物啊，其实有很多种量词的说法……", ""),
        ],
    ),
    dict(
        kp_id="HSK1-ERR-002", explanation="数量短语‘很多’作定语应前置。正确为‘我想买很多苹果’。",
        key_points=[
            {"id": 1, "text": "数量短语‘很多’放在名词前面作定语"},
            {"id": 2, "text": "正确搭配是‘很多+苹果’"},
        ],
        variants=[
            ("pass", "我想买很多苹果。", "很多苹果"),
            ("fail", "我想买苹果很多。", "苹果很多"),
            ("partial", "我想买很多个苹果。", "很多"),  # "很多"前置对, 但缀"个"冗余次要点
            ("hollow", "买东西要多比较，苹果也有很多种。", ""),
        ],
    ),
    dict(
        kp_id="HSK1-ERR-003", explanation="语气词误用：是非疑问用‘吗’。正确为‘你喝水吗？’。",
        key_points=[
            {"id": 1, "text": "是非疑问句句末用疑问词‘吗’"},
            {"id": 2, "text": "疑问语气落在句末的‘吗’上"},
        ],
        variants=[
            ("pass", "你喝水吗？", "吗"),
            ("fail", "你喝水吧？", "吧"),
            ("partial", "你渴了喝吗？", "吗"),  # 用"吗"对, 但引入不必要情态"渴了"次要点
            ("hollow", "喝水这个动作，我觉得很有意义……", ""),
        ],
    ),
    dict(
        kp_id="HSK2-ERR-005", explanation="‘把’字句：把+宾语+动词+补语，‘在桌子上’作结果补语后置。正确为‘我把书放在桌子上了’。",
        key_points=[
            {"id": 1, "text": "用‘把’字句，把+宾语‘书’"},
            {"id": 2, "text": "补语‘在桌子上’放在动词‘放’之后"},
        ],
        variants=[
            ("pass", "我把书放在桌子上了。", "放在桌子上"),
            ("fail", "我把书在桌子上放了。", "在桌子上放了"),
            ("partial", "我把书放上了桌子。", "放上"),  # "把"+补语基本对, 但"上桌子"位置次要点偏移
            ("hollow", "桌子上面放书，这个场景我见过好几次……", ""),
        ],
    ),
    dict(
        kp_id="HSK2-ERR-006", explanation="‘比’字句不能再加‘很’。正确为‘他比我高’。",
        key_points=[
            {"id": 1, "text": "用‘A比B+形容词’句式比较"},
            {"id": 2, "text": "程度由形容词本身表达，比较句里不再加程度副词"},
        ],
        variants=[
            ("pass", "他比我高。", "比我高"),
            ("fail", "他比我很高。", "比我"),
            ("hollow", "人和人之间比较身高，是很自然的事……", ""),
        ],
    ),
    dict(
        kp_id="HSK2-ERR-008", explanation="评述补语：表示能力喜恶应为‘(改)中文说得(不是)很好’。",
        key_points=[
            {"id": 1, "text": "自评语言能力用‘中文说得…’句式"},
            {"id": 2, "text": "表示‘中文’这项界定的‘说得’"},
        ],
        variants=[
            ("pass", "我中文说得不是很好。", "说得"),
            ("fail", "我不能说中文很好。", "不能说"),
            ("hollow", "我最近在学中文，中文很有意思。", ""),
        ],
    ),
    dict(
        kp_id="HSK1-ERR-023", explanation="强调式‘是…的’误用为‘是…了’。正确为‘我是昨天来的’。",
        key_points=[
            {"id": 1, "text": "强调时间状语用‘是…的’结构"},
            {"id": 2, "text": "‘了’本句不能跟在强调结构后，应落在‘的’"},
        ],
        variants=[
            ("pass", "我是昨天来的。", "是昨天来的"),
            ("fail", "我是昨天来了。", "昨天来了"),
            ("hollow", "昨天是个特别的日子，我来这边……", ""),
        ],
    ),
    dict(
        kp_id="HSK2-ERR-025", explanation="词汇搭配：学语言用‘学/学习’，‘做中文’不搭配。正确为‘我努力学习中文’。",
        key_points=[
            {"id": 1, "text": "‘中文’这门语言用‘学习/学’搭配"},
            {"id": 2, "text": "完整说法‘学习中文’，主语+努力+学习+中文"},
        ],
        variants=[
            ("pass", "我努力学习中文。", "学习中文"),
            ("fail", "我努力做中文。", "做中文"),
            ("hollow", "学习中文需要努力，我每天都很努力……", ""),
        ],
    ),
    dict(
        kp_id="HSK2-ERR-030", explanation="缺‘的’：定语标志缺失，应为‘我爸爸开的汽车’。",
        key_points=[
            {"id": 1, "text": "定语修饰‘汽车’，用‘的’连接（开的汽车）"},
            {"id": 2, "text": "‘开的汽车’整体作主语"},
        ],
        variants=[
            ("pass", "我爸爸开的汽车很大。", "开的汽车"),
            ("fail", "我爸爸开汽车很大。", "开汽车"),
            ("hollow", "汽车很大，开起来很稳当……", ""),
        ],
    ),
    dict(
        kp_id="HSK2-ERR-031", explanation="语体不当：问成年人年龄用‘多大了’。",
        key_points=[
            {"id": 1, "text": "对成年人提问年龄用‘多大了’"},
            {"id": 2, "text": "完整说法‘你多大了’"},
        ],
        variants=[
            ("pass", "你多大了？", "多大了"),
            ("fail", "你几岁？", "几岁"),
            ("hollow", "年龄嘛，不同的问法有不同的场合……", ""),
        ],
    ),
]

# ============================================================
# 方向二模板（MuCGEC 真实学习者错误句 → 评测样本）
# source_id 对回 retell_golden.json 的同号句（真实语料可追溯）。
# 关把关注(2026-08-26)：pass 支持"多解(含等价/更优)"；arbitration_note 记录二语检查点；
#   key_points 扩写为"包容等价解"的表述，避免 verifier 对正确改法判未覆盖。
# variants: (truth, restatement, evid) — 同一 truth 可多条（多条 pass=等价解）
# ============================================================
MUCGEC_TEMPLATES = [
    dict(
        source_id="5",  # 学生大概做飞机去北京。
        explanation="动词误用：乘交通工具用‘坐’，‘做’表制作/从事。正确为‘学生大概坐飞机去北京’。",
        key_points=[
            {"id": 1, "text": "表示乘坐交通工具要用动词‘坐’"},
            {"id": 2, "text": "‘飞机’和‘坐’搭配，说‘坐飞机’"},
        ],
        variants=[
            ("pass", "学生大概坐飞机去北京。", "坐飞机"),
            ("fail", "学生大概做飞机去北京。", "做飞机"),
            ("partial", "学生大概做坐飞机去北京。", "坐"),  # 核心"坐"改对, 但仍残留"做"(次要点)
            ("hollow", "坐飞机要提前买票，选靠窗的位置看风景挺美……", ""),
        ],
        arbitration_note="做/坐 同音高频混用；fail(做飞机)干净。已加坐/做英译区分，讲解更扎实。",
    ),
    dict(
        source_id="4",  # 我不愿意这么过分的喜欢歌手。
        explanation="结构助词：副词修饰动词的状语用‘地’。正确为‘我不愿意这么过分地喜欢歌手’。",
        key_points=[
            {"id": 1, "text": "表示程度地修饰‘喜欢’这个动作时，要用状语标记‘地’"},
        ],
        variants=[
            ("pass", "我不愿意这么过分地喜欢歌手。", "过分地"),
            ("fail", "我不愿意这么过分的喜欢歌手。", "过分的"),
            ("partial", "我不愿意过分地喜欢歌手。", "过分地"),  # 核心"地"改对, 漏"这么"程度词(次要点)
            ("hollow", "喜欢歌手要有个度，别太投入就行……", ""),
        ],
        arbitration_note="的/地/得经典混淆区；本句‘喜欢’及物(带宾语‘歌手’)，‘过分地’作状语用‘地’正确，判错不构成过度纠偏。" \
            "(‘过分的喜欢’在‘喜欢’名词化时[如‘这种过分的喜欢’]才合法，本句语境非此，故 feel 成立。)",
    ),
    dict(
        source_id="29",  # 死刑这个问题是特别争议性。
        explanation="缺谓词：名词‘争议性’需要动词来承接。正确为‘死刑这个问题很有争议性’。",
        key_points=[
            {"id": 1, "text": "表达‘有争议性’这个性质，要搭配动词，‘死刑问题很有争议性’"},
        ],
        variants=[
            ("pass", "死刑这个问题很有争议性。", "很有争议性"),          # 更优解
            ("pass", "死刑这个问题争议性很大。", "争议性很大"),          # 等价解
            ("pass", "死刑这个问题很有争议。", "很有争议"),              # 等价解
            ("fail", "死刑这个问题特别争议性。", "特别争议性"),
            ("partial", "死刑这个问题特别具有争议性。", "具有争议性"),    # 补了谓词"具有", 但书面硬(次要点)
            ("hollow", "关于死刑，争议一直都有，大家看法很不一样……", ""),
        ],
        arbitration_note="pass 指定更有自然度的‘很有争议性’为更优解，并纳入‘争议性很大/很有争议’等价解。" \
            "原句‘是’为赘余；fail 保留学习者原样(未补谓词)。",
    ),
    dict(
        source_id="84",  # 最后，提高人们对健康意识。
        explanation="定语结构：母语更常用复合词‘健康意识’。正确为‘最后，提高人们的健康意识’。",
        key_points=[
            {"id": 1, "text": "表达健康观念用固定说法‘健康意识’"},
            {"id": 2, "text": "说‘人们的健康意识’，中间用‘的’连接"},
        ],
        variants=[
            ("pass", "最后，提高人们的健康意识。", "人们的健康意识"),      # 更优解(母语常用复合词)
            ("fail", "最后，提高人们对健康意识。", "对健康意识"),
            ("partial", "最后，提高人们对健康的意识。", "对健康的意识"),    # 已加"的"(保留了"对"), 但复合词解法更自然(次要点)
            ("hollow", "健康很重要，我们要多注意身体……", ""),
        ],
        arbitration_note="学习者‘对健康意识’两解：漏‘的’(保留对) 或 误加‘对’(本应用复合词)。" \
            "把‘健康意识’复合词列为更优 pass，避免自动判分把‘删对’的正确讲解误判为不命中要点。",
    ),
    dict(
        source_id="90",  # 那我们面对这个问题该如此好呢？
        explanation="疑问表达：询问解决办法用‘怎么办’。正确为‘那我们面对这个问题该怎么办呢？’。",
        key_points=[
            {"id": 1, "text": "询问解决办法用疑问表达‘怎么办’"},
        ],
        variants=[
            ("pass", "那我们面对这个问题该怎么办呢？", "怎么办"),
            ("fail", "那我们面对这个问题该如此好呢？", "该如此好"),
            ("partial", "那我们面对这个问题怎么办好呢？", "怎么办"),  # 用了"怎么办"但对偶残留"好"(次要点)
            ("hollow", "每个问题都有解决的办法，我们要好好商量……", ""),
        ],
        arbitration_note="若 pass 亦可为‘该怎么解决(呢)’，MVP 以‘怎么办’为主解，等价解较少，暂不扩多 pass。",
    ),
    dict(
        source_id="147",  # 这是我最后的一封信分开分手
        explanation="固定搭配：告别信说‘分手信’。正确为‘这是我最后的一封分手信’。",
        key_points=[
            {"id": 1, "text": "表示结束恋爱关系的信叫‘分手信’"},
        ],
        variants=[
            ("pass", "这是我最后的一封分手信。", "分手信"),
            ("fail", "这是我最后的一封信分开分手。", "分开分手"),
            ("partial", "这是我最后一封分手的信。", "分手"),  # 提正了"分手", 但"的信"语序仍别扭(次要点)
            ("hollow", "写这封信的时候，我心里特别难过……", ""),
        ],
        arbitration_note="若 pass 可为‘我们最后的信’等，MVP 以‘分手信’主解；等价解较少，暂不扩多 pass。",
    ),
]


def build():
    items = []

    # 方向一（构造集）：补 evid, 保留 need_arbitration
    for tpl in TEMPLATES:
        for (truth, text, evid) in tpl["variants"]:
            items.append(dict(
                id=f"{tpl['kp_id']}:{truth}",
                source="direction1_constructed",
                kp_id=tpl["kp_id"],
                explanation=tpl["explanation"],
                key_points=tpl["key_points"],
                restatement=text,
                evid=evid,
                truth=truth,
                golden_status="draft",
                need_arbitration=(truth in ("hollow", "partial")),
            ))

    # 方向二（MuCGEC 真实学习者句→评测样本）
    for tpl in MUCGEC_TEMPLATES:
        # 同 truth 多解：pass 主解+等价解 → id 加序号后缀区分, 并记录 iso_solution(=1 主解, >1 等价解)
        truth_counter = {}
        for (truth, text, evid) in tpl["variants"]:
            truth_counter[truth] = truth_counter.get(truth, 0) + 1
            n = truth_counter[truth]
            sid = f"MUCGEC-{tpl['source_id']}:{truth}"
            if truth_counter[truth] > 1:
                sid = f"{sid}#{n}"
            items.append(dict(
                id=sid,
                source="direction2_mucgec",
                source_id=tpl["source_id"],
                kp_id=None,
                explanation=tpl["explanation"],
                key_points=tpl["key_points"],
                restatement=text,
                evid=evid,
                truth=truth,
                iso_solution=(n if truth == "pass" else 1),  # pass 多解时标记主解/等价解
                arbitration_note=tpl.get("arbitration_note"),
                golden_status="draft",
                need_arbitration=(truth in ("hollow", "partial")),
            ))

    out = dict(
        meta=dict(
            name="retell_verification", version="0.3",
            purpose="3.3 复述验证黄金集：方向一(构造覆盖变体集)×方向二(MuCGEC 真实学习者句转评测样本) 合并",
            note="truth: pass/fail 由 evid 体现修正要点可判; partial/hollow 语义需你(二语)仲裁 → need_arbitration=True; 方向二 source_id 对回 retell_golden.json; v0.3 关把后 pass 支持多解(用 iso_solution 标主解/等价解)",
            counts=dict(  # 占位, 防手改 clash; 真实统计走 check
                direction1=sum(1 for i in items if i["source"] == "direction1_constructed"),
                direction2=sum(1 for i in items if i["source"] == "direction2_mucgec"),
            ),
        ),
        items=items,
    )
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("生成 retell_verification.json (v0.3)")
    print("  样本总数:", len(items))
    print("  来源分布:", Counter(i["source"] for i in items))
    print("  truth 分布:", Counter(i["truth"] for i in items))
    print("  需仲裁(hollow/partial):", sum(1 for i in items if i["need_arbitration"]))


if __name__ == "__main__":
    build()
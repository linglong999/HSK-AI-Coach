# -*- coding: utf-8 -*-
# ============================================================
# engine/quiz.py · P0.13 独立复习页：确定性挖空造题引擎
# 用法：
#   - 给定复习队列里的 kp，取该 kp 在 grammar_examples 里的例句
#   - 按语法点的 grammar 功能词，在例句中定位关键成分并挖空
#   - 生成 fill 填空题（题干含＿＿、答案、关联 kp、语法点标注）
# 纯标准库 + 确定性规则，无 LLM 依赖；挖不中的例句跳过（保质量，不硬出题）。
# 验收对齐：V2 P0.13 "考纲例句造题多回写"——例句真实被用于出题，并非只当参考。
# ============================================================

import json
import os
import re

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXAMPLES_PATH = os.path.join(_PROJECT_ROOT, "datasets", "grammar_examples.json")
SYLLABUS_PATH = os.path.join(_PROJECT_ROOT, "datasets", "syllabus_hsk30_2025.json")

BLANK = "＿＿"


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _strip_seq(w):
    """去掉 grammar 功能词里的序号/角标：'才4'→'才'、'就5'→'就'、'了2'→'了'。"""
    return re.sub(r'\d+$', '', w)


def _normalize_word(w):
    """把 grammar 词规范化成可匹配的完整功能词。
    - 去括号及内容并拼接（'有（一）点儿'→'有点儿'、'差（一）点儿'→'差'）
    - 去尾部数字序号（'才4'/'就5' 由 _strip_seq 处理）
    - 去模板前缀（'动词+'）
    返回完整的汉字串（'的确'→'的确'、'来得及'→'来得及'），不拆字。"""
    w = w.strip()
    if not w:
        return ""
    w = re.sub(r'^(动词|名词|形容词|数量|代词|动词短语|数量短语|主谓短语)\+', '', w)
    # 去括号内容并拼接前后（'差（一）点儿' → '差点儿'；两段都保留汉字）
    w = re.sub(r'（[^（）]*）|\([^()]*\)', '', w)
    # 仅保留汉字
    return ''.join(re.findall(r'[\u4e00-\u9fff]', w))


def _grammar_words(grammar: str) -> list:
    """从 grammar 字段拆出可挖空的完整功能词，按汉字长度降序、去重。"""
    if not grammar:
        return []
    # 先按分隔符拆（逗号/顿号/斜杠）
    parts = []
    for seg in re.split(r'[、，,；;:／/]', grammar):
        seg = seg.strip()
        if not seg or '+' in seg or seg.startswith('(') or seg.startswith('（'):
            continue
        nw = _normalize_word(seg)
        if nw:
            parts.append(nw)
    # 去序标（'的确'无尾号；'才4'→'才'）
    parts = [_strip_seq(p) for p in parts]
    # 去重、按汉字长度降序（优先长词，减少同形误挖）
    seen, out = set(), []
    for p in sorted((p for p in parts if p), key=len, reverse=True):
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _find_and_mask(sentence: str, word: str):
    """在句子里找 word 第一次出现并挖空。
    功能词（'会/能/上/了/把/被'）是单字虚词，常与邻词紧贴（'你能'、'桌子上'），
    故仅要求 word 不是某个更长词的一部分——由词表长度推理，无需两侧非汉字。
    副作用：可能误挖同形词（'很'在'很少'），由调用方词长优先缓解。"""
    s = sentence.find(word)
    if s < 0:
        return None, None
    e = s + len(word)
    masked = sentence[:s] + BLANK + sentence[e:]
    return masked, sentence[s:e]


def _build_one(kp, ex, grammar_by_kp):
    """为单个 kp 造一道挖空题（能挖到空才返回，否则 None）。"""
    if kp not in ex:
        return None
    grammar = grammar_by_kp.get(kp, "")
    words = _grammar_words(grammar)
    for sent in ex[kp]:
        # no7z 反义对用 / 合并：拆成独立句分别尝试，保证挖空句是单句
        for single in _split_sentences(sent):
            for w in words:
                masked, raw = _find_and_mask(single, w)
                if masked is not None:
                    return {"type": "cloze", "kp_id": kp,
                            "grammar": grammar, "target_word": raw,
                            "sentence": masked, "answer": raw,
                            "full_sentence": single}
    return None


def build_questions(kp_ids=None, limit=None):
    """对给定 kp_ids 集合（或缺省全部有例句的 kp）生成挖空题。
    返回 list[dict]：{kp_id, grammar, sentence, answer, target_word, full_sentence}。"""
    ex = _load_json(EXAMPLES_PATH)["examples"]
    syl = _load_json(SYLLABUS_PATH)["points"]
    grammar_by_kp = {p["id"]: (p.get("grammar") or "") for p in syl}

    targets = set(kp_ids) if kp_ids else set(ex.keys())
    out = []
    for kp in targets:
        q = _build_one(kp, ex, grammar_by_kp)
        if q is not None:
            out.append(q)
    if limit is not None:
        out = out[:int(limit)]
    return out


def build_review_items(queue):
    """把复习队列映射成可做题目的列表，逐 kp 至多一题、保持队列 order。
    - 该 kp 有例句且能挖到空 → {type:'cloze', ...}
    - 否则 → 降级 {type:'recall', kp_id, knowledge_point, level}（针对该点的记忆自检，
      不一定能挖例句空，但可复习调度回写；对齐"无例句点造题时降级处理"）。
    返回 list[dict]。"""
    ex = _load_json(EXAMPLES_PATH)["examples"]
    syl = _load_json(SYLLABUS_PATH)["points"]
    grammar_by_kp = {p["id"]: (p.get("grammar") or "") for p in syl}

    items = []
    for entry in queue:
        kp_id = entry.get("kp_id") or entry.get("id")
        if not kp_id:
            continue
        q = _build_one(kp_id, ex, grammar_by_kp)
        if q is not None:
            items.append(q)
            continue
        node = entry.get("node") or {}
        items.append({
            "type": "recall",
            "kp_id": kp_id,
            "knowledge_point": node.get("knowledge_point") or kp_id,
            "level": node.get("level") or "",
        })
    return items


def _split_sentences(text: str) -> list:
    """按句末标点（。！？；和 /）把一条（可能合并多句的）例句拆成独立句，滤空。"""
    out = []
    for chunk in re.split(r'[。！？；;]|(?=[/])', text):
        chunk = chunk.strip().lstrip('/')
        if chunk:
            out.append(chunk)
    return out


if __name__ == "__main__":
    import sys
    qs = build_questions(kp_ids=sys.argv[1:] or None)
    print(f"生成挖空题 {len(qs)} 条")
    for q in qs[:20]:
        print(f"  {q['kp_id']} [{q['grammar'][:18]}]  {q['sentence']}"
              f"  答案:{q['answer']}")
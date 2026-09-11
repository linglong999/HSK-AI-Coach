# coding=utf-8
# P0.13: 补 10 个 txt 词例空点（有明确 grammar 但 txt 只给词例无完整句），LLM 生成完整句
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.llm.client import LLMClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX_JSON = os.path.join(ROOT, "datasets", "grammar_examples.json")

# 10 个空点的目标：grammar 结构（有结构的补；无结构的留 auto_pending）
TARGETS = [
    ("hsk30-g2-010", "量词：一层/一条裤子/一封信/两件衣服 等搭配"),
    ("hsk30-g2-011", "名量词构句：一条裤子/一位老师/一问房间/一封信 整句"),
    ("hsk30-g2-037", "兼语短语：请他进来/让他做 整句"),
    ("hsk30-g2-039", "名词性短语：我的书/学校的学生 整句"),
    ("hsk30-g2-040", "动词性短语/状中短语 整句"),
    ("hsk30-g2-072", "动词+着 表示状态持续：门开着/灯还亮着 整句"),
    ("hsk30-g3-009", "代词不定指用法：有人/别人 整句"),
    ("hsk30-g3-010", "代词：别人、咱们 整句"),
    ("hsk30-g4-003", "量词：一打/一袋/一棵/一台/一幅 整句"),
    ("hsk30-g4-061", "主语+收/选+宾语1+做/当/为+宾语2：选他当班长 整句"),
]

SYSTEM = """你是中文语法教学助手，为 HSK 语法点生成符合规范的中文例句。
要求：
1. 严格按照给定的语法点结构/搭配生成正确例句，不偏离语法规则。
2. 例句必须是完整中文句子，句末带句号或问号。
3. 难度匹配 HSK 等级。
4. 只返回 JSON 数组（元素为字符串例句），不要任何额外文字。
5. 每种搭配至少覆盖，共 2–4 个例句。"""


def main():
    data = json.load(open(EX_JSON, encoding="utf-8"))
    cli = LLMClient()
    for sid, hint in TARGETS:
        lv = sid.split("-", 2)[1]  # g2 → 2
        prompt = f"HSK{lv} 级语法点，结构：{hint}，请生成 2–4 个符合的完整例句。"
        try:
            res = cli.chat_json(SYSTEM, prompt, temperature=0.15)
            if isinstance(res, list):
                ex = [s for s in res if isinstance(s, str) and s.strip()]
            else:
                ex = res.get("examples") or []
                ex = [s for s in ex if isinstance(s, str) and s.strip()] if isinstance(ex, list) else []
            data["examples"][sid] = ex
            print(f"OK {sid}: {len(ex)} → {ex[:2]}")
        except Exception as e:
            print(f"FAIL {sid}: {e}")
    json.dump(data, open(EX_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("已更新", EX_JSON)


if __name__ == "__main__":
    main()
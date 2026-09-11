# coding=utf-8
# P0.13: 用 LLM 补全 level1-4 缺失语法点例句，写入 grammar_examples.json
import json
import os
import sys
sys.path.insert(0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.llm.client import LLMClient

EX_JSON = os.path.join("datasets", "grammar_examples.json")
MISS_JSON = os.path.join("reports", "_p13_missing_list.json")
OUT_JSON = os.path.join("datasets", "grammar_examples.json")

SYSTEM_PROMPT = """你是中文语法教学助手，为 HSK 语法点生成符合规范的中文例句。
要求：
1. 严格按照给定语法点结构模板生成正确的例句，不得偏离语法规则。
2. 例句必须是完整的中文句子，句末带句号/问号，符合该语法点的使用场景。
3. 难度匹配 HSK 级别（level 1 用常用词，level 4 可用稍微复杂词）。
4. 只返回 JSON 数组（数组元素是字符串，每个字符串是一个例句），不要任何解释、不要多余文字。
5. 如果语法点有多种结构，每种结构至少写一个例句。
6. 生成 2 到 4 个例句。示例：["这句话我听懂了。","你明天能来吗？"]
"""

def main():
    with open(EX_JSON, encoding="utf-8") as f:
        data = json.load(f)
    with open(MISS_JSON, encoding="utf-8") as f:
        missing = json.load(f)
    print(f"读取已有例句: {len(data['examples'])} 条语法点 | 需要补: {len(missing)}")
    cli = LLMClient()
    cnt_done = 0
    for item in missing:
        sid = item["id"]
        lv = item["level"]
        name = item["name"].strip()
        grammar = (item.get("grammar") or "").strip()
        desc = (item.get("desc") or "").strip()
        if grammar:
            prompt = f"语法点：{name}（{grammar}），HSK 等级 {lv}，请生成例句。"
        elif desc:
            prompt = f"语法点：{name}，说明：{desc[:80]}，HSK 等级 {lv}，请生成例句。"
        else:
            prompt = f"语法点：{name}，HSK 等级 {lv}，请生成 2–4 个符合这个语法点的例句。"
        print(f"生成: {sid} {name}")
        try:
            result = cli.chat_json(SYSTEM_PROMPT, prompt, temperature=0.15)
            if isinstance(result, list):
                examples = [s.strip() for s in result if isinstance(s, str) and s.strip()]
            else:
                # 可能返回 {examples: [...]}
                examples = result.get("examples") or []
                if isinstance(examples, list):
                    examples = [s.strip() for s in examples if isinstance(s, str) and s.strip()]
                else:
                    examples = []
            if not examples:
                print(f"  WARN: {sid} 结果为空，跳过")
                continue
            data["examples"][sid] = examples
            cnt_done += 1
            print(f"  OK: {len(examples)} 例 → {examples[:2]}...")
        except Exception as e:
            print(f"  FAIL: {e}")
            continue
    data["metadata"]["coverage"]["matched"] = len(data["examples"])
    data["metadata"]["coverage"]["llm_filled"] = cnt_done
    total_l14 = 339
    matched_l14 = sum(1 for sid in data["examples"] if re.match(r"hsk30-g([1-4])-", sid))
    data["metadata"]["coverage"]["l1-4_total"] = total_l14
    data["metadata"]["coverage"]["l1-4_covered"] = matched_l14
    print(f"\n补完后: l1-4 covered {matched_l14}/{total_l14} ({matched_l14/total_l14:.1%})")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"写入: {OUT_JSON}")


if __name__ == "__main__":
    import re
    main()

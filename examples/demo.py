# ============================================================
# 最小可运行 Demo
# 展示 HSK Coach 的核心闭环：识别 → 费曼讲解 → 复述验证 → 图谱沉淀
# 对齐 v0.3 三引擎架构：轻路由(Router) 串 识别/讲解/验证 + 图谱数据层
# 运行方式：
#   1. 复制 .env.example 为 .env 并填入你的 DEEPSEEK_API_KEY
#   2. python -m examples.demo
# ============================================================

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.router import Router
from datasets.examples.sample_errors import SAMPLE_TEXT, RESTATE_SAMPLE


def main():
    print("=" * 60)
    print("HSK-AI-Coach 最小 Demo")
    print("核心：偏误识别 → 费曼讲解 → 图谱沉淀 → 复述验证")
    print("=" * 60)

    # 轻路由（一个学习者，母语英语，目标 HSK3）
    coach = Router(learner_id="demo_user", native_lang="英语", user_level="HSK3")

    # 处理示例样本
    for i, sample in enumerate(SAMPLE_TEXT, 1):
        print(f"\n\n---------- 样本 {i} ----------")
        print(f"学习者输出：{sample['text']}")
        try:
            result = coach.process(sample["text"], event_key=f"demo-{i}")
            if not result["errors"]:
                print("（未识别到高置信度偏误）")
            for item in result["errors"]:
                e = item["error"]
                conf = e.get("confidence", 0.0)
                print(f"\n· 偏误：{e.get('fragment','')}（{e.get('type','')}，置信度 {conf:.2f}）")
                print(f"  建议：{e.get('correction','')}")
                if item["explanation"]:
                    exp = item["explanation"]
                    print(f"  费曼讲解：{exp.get('explanation','')[:120]}...")
        except Exception as err:
            print(f"  [错误] {err}")

    # 展示偏误图谱（复习队列）
    print("\n\n========== 偏误图谱（复习队列） ==========")
    for item in coach.get_review_queue():
        n = item.get("node", {})
        print(f"· {n.get('knowledge_point','') or item['kp_id']}：出错 {n.get('error_count',0)} 次，优先级 {item['priority']:.2f}")

    # 费曼复述验证
    print("\n\n========== 费曼复述验证 ==========")
    print(f"讲解：{RESTATE_SAMPLE['explanation'][:60]}...")
    key_points = RESTATE_SAMPLE.get("key_points", [])
    try:
        r1 = coach.verify_rephrase(RESTATE_SAMPLE["explanation"], key_points,
                                   RESTATE_SAMPLE["user_rephrase_pass"])
        print(f"通过复述 → {r1.get('verdict')}（覆盖 {r1.get('covered_points')}/{r1.get('total_points')}）")
        r2 = coach.verify_rephrase(RESTATE_SAMPLE["explanation"], key_points,
                                   RESTATE_SAMPLE["user_rephrase_fail"])
        print(f"错误复述 → {r2.get('verdict')} | 引导：{r2.get('feedback','')[:60]}")
    except Exception as err:
        print(f"  [错误] {err}")

    print("\n\nDemo 完成。图谱已保存到 data/ 目录。")


if __name__ == "__main__":
    main()
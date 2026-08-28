# ============================================================
# kp_id 挂接验证：清单 → 2.1 候选注入 → LLM 识别 → knowledge_point_id 命中
# 运行：python run_recognizer.py（在项目根 HSK-AI-Coach/ 下）
# 依赖：已将 DEEPSEEK_API_KEY 设为环境变量（或 .env）
# ============================================================

import os
import sys

# 脚本位于项目根（HSK-AI-Coach/），目录即根
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT_ROOT)

from engine.recognizer import Recognizer, load_knowledge_points
from datasets.examples.sample_errors import SAMPLE_TEXT


def main():
    kps = load_knowledge_points()
    rec = Recognizer()
    print("=" * 62)
    print("kp_id 挂接验证：清单 → 候选注入 → LLM → kp 命中")
    print(f"清单知识点总数：{len(kps)}")
    print("=" * 62)

    total_hit = total_err = 0
    for i, s in enumerate(SAMPLE_TEXT, 1):
        print(f"\n---------- 样本 {i} ----------")
        print(f"学习者输出：{s['text']}")
        try:
            result = rec.recognize(s["text"], level=2, native_lang=s["mother_tongue"])
        except Exception as e:
            print(f"  [识别调用失败] {e}")
            continue

        all_errors = result["errors"] + result["uncertain"]
        if not all_errors:
            print("  （未识别到任何偏误）")
            continue

        for e in all_errors:
            total_err += 1
            kp_id = e["knowledge_point_id"]
            hit = bool(kp_id and e.get("kp_in_list"))
            if hit:
                total_hit += 1
            flag = "✓ 命中" if hit else "✗ 未命中"
            status = "确认" if not e["uncertain"] else "待确认"
            print(f"  [{flag}] ({status}) {e['fragment']} → {e['type']} "
                  f"(conf={e['confidence']:.2f}, kp={kp_id or '空'})")
            if not hit:
                print(f"          kp_in_list={e['kp_in_list']}  uuid_mapped={e.get('uuid_mapped','-')}")

    print("\n" + "=" * 62)
    if total_err:
        print(f"命中率：{total_hit}/{total_err} = {total_hit/total_err:.0%}")
        print("（注：kp_in_list=False 说明 LLM 给的 kp 不在清单内 → 进 2.4 待映射队列，这是预期路径）")
    else:
        print("无偏误样本可统计。")
    print("=" * 62)


if __name__ == "__main__":
    main()
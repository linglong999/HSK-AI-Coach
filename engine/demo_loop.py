# ============================================================
# 模式一 · 最小可运行闭环入口（任务 4.1）
# 单命令跑通：轻路由 Router 串 识别 → 讲解 → 图谱写入 → 复习队列
# 用法:
#   python -m engine.demo_loop "我想买苹果很多。"     # 纠错任一句
#   python -m engine.demo_loop                        # 无参数跑默认样本
# 失败呈现（4.1 验收③）：
#   任一引擎失败 → degraded[] 记录 + 已完成的部分结果照常返回，整体不崩
# 复述验证为独立二次输入接口（router.verify_rephrase），不进单命令闭环
# ============================================================

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.router import Router

DEFAULT_SAMPLE = "我想买苹果很多。"


def run(sentence: str, learner_id: str = "loop_user", native_lang: str = "英语",
        user_level: str = "HSK3") -> dict:
    """跑一次模式一闭环并结构化打印结果（4.1 主证据入口）"""
    coach = Router(learner_id=learner_id, native_lang=native_lang,
                   user_level=user_level)
    # event_key 缺省回退为句子本身（router 内 `{event_key or user_text}#err{i}`）——
    # 稳定幂等键：同一学习者同一句重复演示不重复计数（2.4 幂等设计）。
    # 不用 hash(句子)：Python 字符串 hash 每进程随机化，会破坏幂等。
    result = coach.process(sentence)

    print("=" * 62)
    print(f"学习者输出：{sentence}")
    print("=" * 62)

    if not result["errors"]:
        print("（未识别到高置信度偏误——句子可能正确，或偏误进入待确认）")

    for i, item in enumerate(result["errors"], 1):
        e = item["error"]
        print(f"\n—— 偏误 {i} ——")
        print(f"片段：{e.get('fragment', '')}（{e.get('type', '')}，"
              f"置信度 {float(e.get('confidence', 0)):.2f}）")
        print(f"建议修正：{e.get('correction', '')}")
        print(f"知识点：{e.get('knowledge_point_id') or '（清单外，进待映射）'}")

        expl = item.get("explanation") or {}
        if expl.get("_degraded"):
            print(f"费曼讲解：[讲解引擎降级] {expl.get('explanation', '')}")
        else:
            print(f"费曼讲解：\n{expl.get('explanation', '')}")
            kps = expl.get("key_points") or []
            if kps:
                print("复述要点：")
                for kp in kps:
                    print(f"  · {kp.get('text', kp) if isinstance(kp, dict) else kp}")

        gw = item.get("graph_write") or {}
        print(f"图谱写入：{gw.get('status', '?')}"
              + (f" → kp {gw.get('kp_id')}" if gw.get("kp_id") else ""))

    queue = result.get("review_queue") or []
    if queue:
        print("\n========== 复习队列（priority 降序 top5） ==========")
        for q in queue[:5]:
            n = q.get("node", {})
            print(f"· {n.get('knowledge_point') or q.get('kp_id')}："
                  f"出错 {n.get('error_count', 0)} 次，"
                  f"优先级 {q.get('priority', 0):.2f}")

    if result.get("degraded"):
        print("\n!! 降级记录（部分引擎失败，其余链路照常完成）!!")
        for d in result["degraded"]:
            stage = d.get("stage", "?") if isinstance(d, dict) else "?"
            reason = d.get("reason", d) if isinstance(d, dict) else d
            fatal = " [致命]" if isinstance(d, dict) and d.get("fatal") else ""
            print(f"  - [{stage}]{fatal} {reason}")
    else:
        print("\n全链路无降级：识别 / 讲解 / 图谱 全部成功。")

    print(f"\n图谱规模：{result.get('graph_size', 0)} 个知识点节点"
          f"（已保存到 data/graph_{learner_id}.json）")
    return result


def main():
    sentence = " ".join(sys.argv[1:]).strip() or DEFAULT_SAMPLE
    run(sentence)


if __name__ == "__main__":
    main()
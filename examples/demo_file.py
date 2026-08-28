# ============================================================
# 文件讲解（模式二）Demo
# 复现"打开一篇中文文件 → 选中不同片段 → 触发讲解通道"
# 展示两路分流：
#   - 选中含偏误的句子 → 纠错讲解 + 图谱沉淀（source=file_mode）
#   - 选中正确的句子/短语 → 泛讲解（讲含义+语言点），不写图谱
# 运行：python -m examples.demo_file  （需配置 DEEPSEEK_API_KEY）
# ============================================================

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.file_mode import FileCoach


# 模拟用户"打开"的一篇中文文件（学习者日记）。注释标注选中哪个片段应走哪一路。
FILE_TEXT = """自从来到中国，我每天都有一个新的学到的东西。
有一天我在公园看见一个可爱的小狗，我很想抱它。
虽然学习中文很辛苦，但是如果每天都练，就一定会进步。
我决定以后要多用中文和邻居聊天，因为这样我才能融入这里。"""

# 用户"选中"的不同片段 → 各自的预期行为
SELECTIONS = [
    # 偏误句：量词"个"误用（小狗应"只"）→ 纠错链 + 图谱
    ("偏误句", "我看见一个可爱的小狗，我很想抱它。"),
    # 正确复杂句：想弄懂"虽然…但是…"结构 → 泛讲解，不写图谱
    ("正确复杂句", "虽然学习中文很辛苦，但是如果每天都练，就一定会进步。"),
    # 正确地道短语：想知道地道说法 → 泛讲解
    ("正确短语", "融入这里"),
]


def main():
    print("=" * 60)
    print("HSK-AI-Coach 文件讲解（模式二）Demo")
    print("选中哪讲哪 · 两路分流 · 复用引擎不重写")
    print("=" * 60)

    coach = FileCoach(learner_id="file_demo", native_lang="英语", user_level="HSK3")

    for i, (label, sel) in enumerate(SELECTIONS, 1):
        print(f"\n\n---------- 选中 {label} ----------")
        print(f"选中片段：{sel}")
        try:
            r = coach.explain_selection(sel, event_key=f"file-demo-{i}")
            if r["has_error"]:
                for e in r["errors"]:
                    err = e["error"]
                    print(f"[纠错链] 偏误：{err.get('fragment','')}（{err.get('type','')}）")
                    print(f"          建议：{err.get('correction','')}")
                    exp = e["explanation"]
                    print(f"          费曼讲解：{exp.get('explanation','')[:110]}...")
                    print(f"          图谱沉淀：{e['graph_write']}")
            else:
                p = r["plain"] or {}
                print(f"[泛讲解] 讲解：{p.get('explanation','')[:110]}...")
                print(f"          要点 {len(p.get('key_points',[]))} 个 · 未写图谱（正确句不污染）")
        except Exception as ex:
            print(f"  [错误] {ex}")

    print("\n\n========== 吃完后复习队列（只含偏误沉淀） ==========")
    for item in coach.review_queue():
        n = item.get("node", {})
        print(f"· {n.get('knowledge_point','') or item['kp_id']}：出错 {n.get('error_count',0)} 次，优先级 {item['priority']:.2f}")


if __name__ == "__main__":
    main()
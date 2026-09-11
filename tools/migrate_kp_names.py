# -*- coding: utf-8 -*-
# ============================================================
# tools/migrate_kp_names.py · P0.19 根因A数据迁移
#
# 背景：早前识别器只回传 knowledge_point_id、不传 knowledge_point_name，
# 图谱节点 knowledge_point 退化为 kp_id（如 "kp-liangci"），前端认知地图/
# 复习安排/集中复习满屏裸显示 id。engine/graph/error_graph.py 已修为新建节点
# 自动查表回填人读名称；本脚本对**历史落盘**的 graph_*.json 做一次性回填：
#   knowledge_point == kp_id  → 用 knowledge_points_v1_4.json 的名称覆盖。
# 仅当图谱节点名恰好等于 id 时才覆盖（避免覆盖人工起名/非标准名）。
#
# 用法：python -m tools.migrate_kp_names [要迁移的 file ...]
#       缺省扫描 data/graph_*.json。
# 幂等可重跑：已回填的节点名不再等于 id，二次运行不重复覆盖。
# ============================================================

import glob
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
KP_PATH = os.path.join(_PROJECT_ROOT, "datasets", "knowledge_points_v1_4.json")


def _load_names() -> dict:
    with open(KP_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    names = {}
    for kpid, kp in (raw.get("knowledge_points") or {}).items():
        if kp.get("knowledge_point"):
            names[kpid] = kp["knowledge_point"]
    return names


def _iter_node_dicts(graph):
    """图结构可能形如 {'nodes': {...}} 或直接 {kp_id: node} 两种，兼容返回节点 dict 迭代。"""
    if isinstance(graph, dict) and isinstance(graph.get("nodes"), dict):
        return graph["nodes"]
    if isinstance(graph, dict):
        return graph
    return {}


def migrate(path: str, names: dict) -> int:
    with open(path, encoding="utf-8") as f:
        g = json.load(f)
    nodes = _iter_node_dicts(g)
    changed = 0
    for kpid, node in nodes.items():
        if not isinstance(node, dict):
            continue
        cur = node.get("knowledge_point")
        # 只有"名==id"这种退化时才回填；名非 id（含已回填/人工名）不碰
        if cur is None or str(cur) == str(kpid):
            good = names.get(kpid)
            if good:
                node["knowledge_point"] = good
                changed += 1
    if changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(g, f, ensure_ascii=False, indent=2)
    return changed


def main(argv):
    names = _load_names()
    print(f"名称表条目：{len(names)}")
    targets = argv[1:] or sorted(glob.glob(os.path.join(DATA_DIR, "graph_*.json")))
    total = 0
    for p in targets:
        if not os.path.exists(p):
            print(f"  跳过(不存在): {p}")
            continue
        try:
            n = migrate(p, names)
            total += n
            print(f"  {'回填 %d 个节点':<6} {os.path.basename(p)}" % n)
        except Exception as e:  # noqa: BLE001
            print(f"  跳过(格式非预期): {os.path.basename(p)} — {e}")
    print(f"完成，共回填 {total} 个节点。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
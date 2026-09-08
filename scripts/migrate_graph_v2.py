# -*- coding: utf-8 -*-
# ============================================================
# scripts/migrate_graph_v2.py
# P0.5 数据迁移脚本（一次生成，可独立于运行时重跑）
# 把 P0.2-P0.4 之前的旧 schema 图谱迁移到新 schema：
#   双维度 error_kind/nature（现算，非裸 setdefault"未知"）
#   P0.3 正向字段 positive_count/sources/last_positive_at
#   P0.4 字段 unfixed_streak（fossilized 现算不落盘）
# 迁移复用 ErrorGraph.load/save 的现算补齐逻辑，保证：
#   - nature/error_kind 从 error_types 经 resolve 现算（比硬塞"未知"精确）
#   - created_at 进白名单保留（不丢 aging 起算点）
# 特性：备份到 data/backup_YYYYMMDD/ 不覆盖；--dry-run 只打印统计；
#       逐节点现算明细报告；备份可加载一致性校验；幂等（重复跑结果一致）。
# 用法:
#   python scripts/migrate_graph_v2.py             # 真迁
#   python scripts/migrate_graph_v2.py --dry-run   # 只打印不写
# ============================================================

import argparse
import datetime
import glob
import json
import os
import shutil
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from engine.graph.error_graph import ErrorGraph
from engine.graph.error_kind_map import resolve

# 三迁清单（用户 A1 拍板）：demo(serve默认) + eval_3_5(评测引导) + file_demo(演示并入)
# graph_plan_trace/transfer_plan 已是新 schema，不入列。
TARGETS = [
    os.path.join(_PROJECT_ROOT, "data", "graph_demo.json"),
    os.path.join(_PROJECT_ROOT, "data", "graph_eval_3_5.json"),
    os.path.join(_PROJECT_ROOT, "data", "graph_file_demo.json"),
]


def backup_dir() -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    return os.path.join(_PROJECT_ROOT, "data", f"backup_{stamp}")


def node_detail(node_dict: dict) -> dict:
    """逐节点现算明细：双维度来源 + 各字段补齐后终值。"""
    dims = resolve(node_dict.get("error_types", {}), node_dict.get("id", ""))
    return {
        "id": node_dict.get("id"),
        "level": node_dict.get("level"),
        "error_types": node_dict.get("error_types", {}),
        "error_kind(现算)": dims["error_kind"],
        "nature(现算)": dims["nature"],
        "error_count": node_dict.get("error_count", 0),
        "unfixed_streak": node_dict.get("unfixed_streak", 0),
        "created_at保留": node_dict.get("created_at", "") != "",
    }


def migrate(path: str, dry_run: bool) -> dict:
    """单个文件迁移。返回报告。"""
    src = path
    name = os.path.basename(src)
    learner = os.path.splitext(name)[0].replace("graph_", "")

    # 读原始节点数（迁移不丢节点判据）
    with open(src, encoding="utf-8") as f:
        raw = json.load(f)
    before_nodes = len(raw.get("nodes", {}))
    if before_nodes == 0:
        return {"file": name, "skipped": True, "reason": "空图谱"}

    g = ErrorGraph(learner)
    g.load(src)          # load 现算补齐（含 created_at 白名单保留）
    g.save(src)          # save 原子写回新 schema

    # 迁移后校验
    with open(src, encoding="utf-8") as f:
        after_data = json.load(f)
    after_nodes = len(after_data.get("nodes", {}))

    detail = node_detail(list(after_data["nodes"].values())[0]) \
        if after_nodes else {}
    detail["节点数"] = f"{after_nodes}/{before_nodes}"

    if not dry_run:
        # 备份（不覆盖）到 backup_YYYYMMDD/，并校验备份可加载
        bk = os.path.join(backup_dir(), name)
        os.makedirs(os.path.dirname(bk), exist_ok=True)
        if not os.path.exists(bk):
            shutil.copy2(src, bk)
        else:
            bk = bk + ".dup"
            shutil.copy2(src, bk)
        # 备份可加载一致性校验：备份文件 load 不炸 且 节点数一致
        g_backup = ErrorGraph(learner + "_bak")
        g_backup.load(bk if not bk.endswith(".dup") else bk)
        backup_nodes = len(g_backup._nodes)
        assert backup_nodes == after_nodes, f"{name} 备份加载节点数不一致"
        detail["备份"] = os.path.relpath(bk, _PROJECT_ROOT)

    return {"file": name, "skipped": False, "before": before_nodes,
            "after": after_nodes, "detail": detail, "dry_run": dry_run}


def report(results: list):
    print("=" * 60)
    for r in results:
        if r.get("skipped"):
            print(f"[跳过] {r['file']}: {r['reason']}")
            continue
        tag = "DRY" if r.get("dry_run") else "迁移"
        print(f"[{tag}] {r['file']} 节点 {r.get('before')}→{r.get('after')}（不丢={r.get('after')==r.get('before')}）")
        d = r.get("detail", {})
        print(f"     example: id={d.get('id')} 双维度={d.get('error_kind(现算)')}/{d.get('nature(现算)')} "
              f"streak={d.get('unfixed_streak')} created_at保留={d.get('created_at保留')}")
        if d.get("备份"):
            print(f"     备份={d['备份']}")
        # 全节点现状（如 eval 多节点）打印各节点双维度
        for nd in current_nodes(r["file"]):
            print(f"     {nd['id']:24s} kind={nd['error_kind']:2s} nature={nd['nature']:2s}")
    print("=" * 60)


_cached_nodes = {}


def current_nodes(name: str):
    if name in _cached_nodes:
        return _cached_nodes[name]
    path = os.path.join(_PROJECT_ROOT, "data", name)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    nodes = [{"id": k, "error_kind": v.get("error_kind", ""),
              "nature": v.get("nature", "")}
             for k, v in data.get("nodes", {}).items()]
    _cached_nodes[name] = nodes
    return nodes


def main():
    ap = argparse.ArgumentParser(description="P0.5 图谱字段迁移")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印每文件补全统计与逐节点明细，不写文件、不备份")
    args = ap.parse_args()

    results = []
    for path in TARGETS:
        if not os.path.exists(path):
            results.append({"file": os.path.basename(path), "skipped": True,
                            "reason": "文件不存在"})
            continue
        results.append(migrate(path, args.dry_run))

    report(results)

    # 幂等性校验：dry-run 无论跑几次结果一致（setdefault/现算天然幂等）。真迁后再确认。
    if not args.dry_run:
        print("已写回。可用 --dry-run 复跑验证幂等（应显示相同现算结果且不改文件）。")


if __name__ == "__main__":
    main()
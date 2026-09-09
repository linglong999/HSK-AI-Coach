# ============================================================
# engine/textbook_map.py
# P0.14 · HSK 教材对齐（《HSK 标准教程》1-4）
# 以考纲为桥：考纲点(kp.syllabus_refs 的 hsk30-gX-YYY) → 教材课次 → 场景
# 只存"课次定位 + 考纲点关联"，不搬教材原文（版权安全）。
# 查询全部可兜底：未映射/超教材范围 → 返回 []（调用方按通用库处理，不报错）。
# 支持多教材配置（books{}.units），active 切换即换教材。
# ============================================================

import json
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join(_PROJECT_ROOT, "datasets", "textbook_map.json")

_map_cache = None


def load_map(path: str = MAP_PATH) -> dict:
    """加载教材映射配置（进程内缓存）。文件缺失/损坏 → 空结构（查询全部兜底，不阻断）。"""
    global _map_cache
    if _map_cache is not None:
        return _map_cache
    empty = {"active": "stdcourse_hsk", "books": {}, "note": "教材映射缺失/损坏，查询兜底"}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("books"), dict):
            data = empty
    except Exception:
        data = empty
    _map_cache = data
    return _map_cache


def active_book(map_data: dict = None) -> str:
    """当前激活教材 key。无激活/未知 → 取默认空字符串（查询兜底）。"""
    d = map_data or load_map()
    return str(d.get("active", "") or "")


def units_of(book_key: str, map_data: dict = None) -> list:
    """取某教材的 units 列表（可能为空）。未知教材 → []。"""
    d = map_data or load_map()
    book = d.get("books", {}).get(book_key)
    if not isinstance(book, dict):
        return []
    units = book.get("units", [])
    return units if isinstance(units, list) else []


def _lesson_of(unit: dict) -> dict:
    """把单个 unit 抽象为定位信息（卷/课/标题），无则空。"""
    return {
        "volume": unit.get("volume", ""),
        "lesson": unit.get("lesson"),
        "title": unit.get("title", ""),
    }


def lessons_for_syllabus_ids(syllabus_ids, book_key: str = None, map_data: dict = None) -> list:
    """给定一批考纲点 id（hsk30-gX-YYY），返回命中的课次定位列表。
    任一 unit 的 kp_ids 与之相交即命中；重复考点去重。未命中 → []（通用兜底）。"""
    d = map_data or load_map()
    key = book_key or active_book(d)
    ids = {str(i) for i in (syllabus_ids or []) if i}
    if not ids:
        return []
    hits, seen = [], set()
    for u in units_of(key, d):
        un_ids = {str(i) for i in (u.get("kp_ids") or []) if i}
        if ids & un_ids:
            loc = _lesson_of(u)
            sig = (loc["volume"], str(loc["lesson"]), loc["title"])
            if sig not in seen:
                seen.add(sig)
                hits.append(loc)
    return hits


def lessons_for_kp(kp: dict, book_key: str = None, map_data: dict = None) -> list:
    """给定一个已入库知识点记录（含 syllabus_refs），返回对应教材课次。
    syllabus_refs 中 value=true 的才是确认映射；false 视为存疑不参与。"""
    refs = kp.get("syllabus_refs") if isinstance(kp, dict) else None
    if not isinstance(refs, dict):
        return []
    ids = [rid for rid, ok in refs.items() if ok]
    return lessons_for_syllabus_ids(ids, book_key, map_data)


def lessons_for_scene(scene: dict, book_key: str = None, map_data: dict = None) -> list:
    """给定一个场景记录（含 kp_ids），返回其覆盖知识点对应的教材课次。"""
    if not isinstance(scene, dict):
        return []
    return lessons_for_syllabus_ids(scene.get("kp_ids"), book_key, map_data)
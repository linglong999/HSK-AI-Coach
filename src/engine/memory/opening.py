"""开场续接、上一段会话扫描与经历摘要编译。"""

from engine.memory.extractor import EXTRACTOR_VERSION, extract_candidates, should_extract
from engine.memory.long_term import LongTermMemoryError
from engine.scenarios import get_scene


class _ObservedClient:
    """提取器对外吞异常；编排层仍需知道调用是否失败以允许重试。"""

    def __init__(self, client):
        self.client = client
        self.failed = False

    def chat_json_strict(self, **kwargs):
        try:
            return self.client.chat_json_strict(**kwargs)
        except Exception:
            self.failed = True
            raise


def build_opening(mem, ltm, conversation_id: str, *, enabled: bool,
                  client_factory, native_lang: str = "") -> dict:
    out = {"enabled": enabled, "resume": None, "candidates": [], "notices": []}
    if not enabled:
        return out
    prev = mem.previous_session(conversation_id)
    if prev:
        scene = get_scene(prev["scene_id"]) if prev["scene_id"] else None
        if scene:
            out["resume"] = {"scene_id": prev["scene_id"], "title": scene["title"],
                             "title_zh": scene["title_zh"], "conversation_id": prev["session_id"]}
        if prev["memory_scanned_at"] is None:
            texts = [msg["content"] for msg in mem.get_history(
                prev["session_id"], window=mem.PERSIST_WINDOW) if msg["role"] == "user"]
            scanned = True
            try:
                if should_extract(texts):
                    client = client_factory()
                    if client is None:
                        scanned = False
                        out["notices"].append({"stage": "memory_extract", "reason": "no_provider"})
                    else:
                        observed = _ObservedClient(client)
                        candidates = extract_candidates(texts, client=observed, native_lang=native_lang)
                        if observed.failed:
                            raise RuntimeError("extract_failed")
                        for i, content in enumerate(candidates):
                            try:
                                ltm.add_candidate("life_event", content, {
                                    "conversation_id": prev["session_id"], "extractor": EXTRACTOR_VERSION,
                                    "event_key": f"{prev['session_id']}:{i}"})
                            except LongTermMemoryError:
                                continue
            except Exception:
                scanned = False
                out["notices"].append({"stage": "memory_extract", "reason": "extract_failed"})
            if scanned:
                mem.mark_memory_scanned(prev["session_id"])
    out["candidates"] = [{key: item[key] for key in (
        "memory_id", "kind", "content", "created_at")} for item in ltm.list_items("candidate")]
    return out


def compile_memory_brief(items: list[dict], native_lang: str = "") -> str:
    if not items:
        return ""
    header = (
        "[Memory] Learner-confirmed personal experiences (mention naturally when appropriate "
        "to introduce practice topics; do not recite the list, probe privacy, or treat it as a test):"
        if native_lang and native_lang.lower() != "zh" else
        "[Memory] 学习者确认过的个人经历（可在合适时自然提起，用来引出练习话题；不要逐条复述，不要追问隐私，不要当作考题）：")
    lines = [header]
    for item in items:
        content = item["content"]
        extra = "/".join(content[key] for key in ("place", "when") if content.get(key))
        lines.append(f"- {content['summary']}" + (f" ({extra})" if extra else ""))
    return "\n".join(lines)

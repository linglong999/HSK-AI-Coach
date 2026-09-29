"""单机进程内 API 运行统计；不保存请求内容、路径参数或密钥。"""

from collections import defaultdict, deque
from threading import Lock


class OpsMetrics:
    def __init__(self, *, window: int = 256):
        self._lock = Lock()
        self._routes = defaultdict(lambda: {"requests": 0, "failures": 0,
                                           "durations_ms": deque(maxlen=window)})

    def record(self, method: str, route: str, status: int, duration_ms: float) -> None:
        key = f"{method} {route}"
        with self._lock:
            entry = self._routes[key]
            entry["requests"] += 1
            entry["failures"] += status >= 500
            entry["durations_ms"].append(round(max(0.0, duration_ms), 2))

    def snapshot(self) -> dict:
        with self._lock:
            routes = {}
            for name, entry in sorted(self._routes.items()):
                values = sorted(entry["durations_ms"])
                routes[name] = {
                    "requests": entry["requests"],
                    "failures": entry["failures"],
                    "failure_rate": round(entry["failures"] / entry["requests"], 4),
                    "latency_p95_ms": values[max(0, (95 * len(values) + 99) // 100 - 1)]
                    if values else None,
                    "latency_samples": len(values),
                }
        return {"scope": "current_process", "routes": routes,
                "latency_note": "HTTP response headers only; SSE stream duration excluded"}

"""P3-2：请求追踪与聚合统计不得保留用户输入。"""

from fastapi.testclient import TestClient
from unittest import mock

from engine.ops_metrics import OpsMetrics
from engine.serve import create_app


def test_ops_metrics_aggregates_bounded_latency():
    metrics = OpsMetrics(window=2)
    for duration, status in ((10, 200), (30, 500), (20, 200)):
        metrics.record("POST", "/api/dialog", status, duration)
    route = metrics.snapshot()["routes"]["POST /api/dialog"]
    assert route == {"requests": 3, "failures": 1,
                     "failure_rate": 0.3333, "latency_p95_ms": 30,
                     "latency_samples": 2}


def test_request_id_and_summary_do_not_expose_query_or_secret(tmp_path, caplog):
    app = create_app(object(), str(tmp_path), memory_root=str(tmp_path))
    client = TestClient(app, base_url="http://127.0.0.1:8612")
    secret = "sk-fake-should-not-appear"
    with caplog.at_level("INFO", logger="uvicorn.error"):
        first = client.get(f"/api/absent?api_key={secret}")
        second = client.get("/api/absent?other=private")
        provider_list = client.get("/api/providers")
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
    assert len(first.headers["X-Request-ID"]) == 16
    summary = client.get("/api/ops/summary")
    assert summary.status_code == 200
    assert summary.headers["X-Request-ID"]
    assert secret not in summary.text
    assert "private" not in summary.text
    assert secret not in caplog.text
    assert "private" not in caplog.text
    assert first.headers["X-Request-ID"] in caplog.text
    assert provider_list.status_code == 200
    assert summary.json()["scope"] == "current_process"
    assert sum(row["requests"] for row in summary.json()["routes"].values()) >= 2
    assert "GET /api/providers" in summary.json()["routes"]
    assert all("?" not in route for route in summary.json()["routes"])


def test_ops_snapshot_is_per_app(tmp_path):
    first = TestClient(create_app(object(), str(tmp_path), memory_root=str(tmp_path)),
                       base_url="http://127.0.0.1:8612")
    other = TestClient(create_app(object(), str(tmp_path), memory_root=str(tmp_path)),
                       base_url="http://127.0.0.1:8612")
    first.get("/api/absent")
    assert other.get("/api/ops/summary").json()["routes"] == {}


def test_unhandled_exception_has_trace_id_but_no_secret(tmp_path, caplog):
    secret = "sk-fake-never-log-this"
    app = create_app(object(), str(tmp_path), memory_root=str(tmp_path))
    client = TestClient(app, base_url="http://127.0.0.1:8612")
    with mock.patch("engine.providers.effective_providers",
                    side_effect=RuntimeError(secret)):
        with caplog.at_level("INFO", logger="uvicorn.error"):
            response = client.get("/api/providers")
    assert response.status_code == 500
    assert response.json() == {"error": "internal_error"}
    assert response.headers["X-Request-ID"] in caplog.text
    assert secret not in response.text + caplog.text
    assert client.get("/api/ops/summary").json()["routes"]["GET /api/providers"]["failures"] == 1

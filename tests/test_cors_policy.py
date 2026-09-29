"""P1：跨域来源必须显式列入 allowlist。"""

import tempfile
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from engine.serve import create_app


def _app(root):
    # CORS 只验证中间件；具体 API 可用空路由对象构造。
    return create_app(object(), root, memory_root=root)


def _client(app):
    return TestClient(app, base_url="http://127.0.0.1:8612")


def test_default_same_origin_only():
    with tempfile.TemporaryDirectory() as root:
        with mock.patch("config.settings.CORS_ALLOW_ORIGINS", ""):
            app = _app(root)
        response = _client(app).get("/api/providers", headers={"Origin": "https://evil.example"})
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers


def test_explicit_origin_allowlist():
    with tempfile.TemporaryDirectory() as root:
        with mock.patch("config.settings.CORS_ALLOW_ORIGINS", "https://coach.example"):
            app = _app(root)
        client = _client(app)
        allowed = client.get("/api/providers", headers={"Origin": "https://coach.example"})
        denied = client.get("/api/providers", headers={"Origin": "https://evil.example"})
        assert allowed.headers["access-control-allow-origin"] == "https://coach.example"
        assert "access-control-allow-origin" not in denied.headers


def test_wildcard_rejected():
    with tempfile.TemporaryDirectory() as root:
        with mock.patch("config.settings.CORS_ALLOW_ORIGINS", "*"):
            with pytest.raises(ValueError, match="通配符"):
                _app(root)


def test_cross_site_write_rejected_even_without_cors_response():
    with tempfile.TemporaryDirectory() as root:
        with mock.patch("config.settings.CORS_ALLOW_ORIGINS", ""):
            app = _app(root)
        client = _client(app)
        response = client.post(
            "/api/providers", json={"name": "evil", "base_url": "https://evil.example/v1",
                                    "api_key": "secret", "model": "m"},
            headers={"Origin": "https://evil.example"})
        assert response.status_code == 403
        assert response.json()["error"] == "cross_origin_write_denied"
        assert not any(p.get("name") == "evil" for p in client.get("/api/providers").json()["providers"])


def test_same_origin_and_cli_writes_remain_available():
    with tempfile.TemporaryDirectory() as root:
        with mock.patch("config.settings.CORS_ALLOW_ORIGINS", ""):
            app = _app(root)
        client = _client(app)
        payload = {"name": "T", "base_url": "https://api.example/v1", "api_key": "secret", "model": "m"}
        same = client.post("/api/providers", json=payload,
                           headers={"Origin": "http://127.0.0.1:8612"})
        assert same.status_code == 200
        assert client.post("/api/providers", json=payload).status_code == 200


def test_fetch_site_cross_site_without_origin_rejected():
    with tempfile.TemporaryDirectory() as root:
        app = _app(root)
        response = _client(app).post(
            "/api/providers", json={}, headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 403


def test_dns_rebinding_host_cannot_be_treated_as_local_same_origin():
    with tempfile.TemporaryDirectory() as root:
        app = _app(root)
        client = TestClient(app, base_url="http://evil.example")
        response = client.post("/api/providers", json={},
                               headers={"Origin": "http://evil.example"})
        assert response.status_code == 403
        assert response.json()["error"] == "cross_origin_write_denied"


def test_dns_rebinding_host_cannot_read_local_data():
    with tempfile.TemporaryDirectory() as root:
        app = _app(root)
        client = TestClient(app, base_url="http://evil.example")
        response = client.get("/api/providers")
        assert response.status_code == 403
        assert response.json()["error"] == "invalid_host"

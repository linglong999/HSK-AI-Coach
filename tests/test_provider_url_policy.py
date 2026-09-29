"""P1 单机供应商 URL 出口预校验；公网仍需连接层 DNS 固定。"""

import socket
import tempfile
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from engine import providers
from engine.serve import create_app


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://192.168.1.2/v1",
    "http://example.com/v1",
    "https://127.0.0.1/v1",
    "https://[::1]/v1",
    "https://10.0.0.1/v1",
    "file:///etc/passwd",
    "https://user:pass@example.com/v1",
    "https://example.com/v1?token=secret",
])
def test_rejects_dangerous_url_without_network(url):
    with pytest.raises(ValueError):
        providers.validate_provider_url(url, resolve_dns=False)


def test_local_ollama_requires_explicit_opt_in():
    with pytest.raises(ValueError):
        providers.validate_provider_url("http://127.0.0.1:11434/v1")
    assert providers.validate_provider_url(
        "http://127.0.0.1:11434/v1", allow_local=True) == "http://127.0.0.1:11434/v1"


def test_dns_private_answer_rejected():
    private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.10", 443))]
    with mock.patch("engine.providers.socket.getaddrinfo", return_value=private):
        with pytest.raises(ValueError, match="非公网"):
            providers.validate_provider_url("https://api.example.com/v1")


def test_dns_public_answer_allowed():
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    with mock.patch("engine.providers.socket.getaddrinfo", return_value=public):
        assert providers.validate_provider_url("https://api.example.com/v1") == "https://api.example.com/v1"


def test_endpoint_rejects_invalid_url_without_storing_key():
    with tempfile.TemporaryDirectory() as root:
        app = create_app(object(), root, memory_root=root)
        client = TestClient(app, base_url="http://127.0.0.1:8612")
        response = client.post("/api/providers", json={
            "name": "bad", "base_url": "http://169.254.169.254/latest/",
            "api_key": "sk-do-not-leak", "model": "m"})
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_provider_url"
        assert "sk-do-not-leak" not in response.text
        assert not any(p.get("name") == "bad" for p in client.get("/api/providers").json()["providers"])


def test_connection_test_never_calls_llm_for_private_literal():
    with mock.patch("engine.llm.client.LLMClient.chat") as chat:
        result = providers.test_provider("https://10.0.0.1/v1", "sk-do-not-leak", "m")
    assert result["ok"] is False
    assert "sk-do-not-leak" not in str(result)
    chat.assert_not_called()

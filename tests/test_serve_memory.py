from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from engine.memory.learner_memory import LearnerMemory
from engine.memory.long_term import LongTermMemory
from engine.serve import create_app, _memory_client_factory
from tests.test_serve_sessions import FakeRouter
from tests.test_memory_extractor import FakeClient
from tests._serve_common import parse_body


@pytest.fixture
def api(tmp_path):
    router = FakeRouter()
    app = create_app(router, str(tmp_path), memory_root=str(tmp_path),
                     dialog_llm=lambda messages: '[{"type":"text","content":"好的"}]')
    mem = LearnerMemory(router.learner_id, root=str(tmp_path))
    ltm = LongTermMemory(router.learner_id, root=str(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, mem, ltm
    mem._conn.close()
    ltm._conn.close()


def test_opening_endpoint(api):
    client, mem, ltm = api
    mem.append("old", "user", "我上周去了长城")
    fake = FakeClient()
    with patch("engine.serve._memory_client_factory", return_value=fake):
        first = client.post("/api/memory/opening", json={"conversation_id": "new"})
        second = client.post("/api/memory/opening", json={"conversation_id": "new"})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(first.json()["candidates"]) == len(fake.calls) == 1
    assert "source" not in first.text
    for body in ({}, {"conversation_id": 3}, []):
        assert client.post("/api/memory/opening", json=body).status_code == 400


def test_opening_no_provider_and_disabled(api):
    client, mem, _ = api
    mem.append("old", "user", "我上周去了长城")
    with patch("engine.serve._memory_client_factory", return_value=None):
        response = client.post("/api/memory/opening", json={"conversation_id": "new"})
    assert response.json()["notices"] == [{"stage": "memory_extract", "reason": "no_provider"}]
    assert mem.previous_session("new")["memory_scanned_at"] is None
    mem.update_profile(memory_enabled=False)
    with patch("engine.serve._memory_client_factory", side_effect=AssertionError("disabled")) as factory:
        response = client.post("/api/memory/opening", json={"conversation_id": "new"})
    factory.assert_not_called()
    assert response.json() == {"enabled": False, "resume": None, "candidates": [], "notices": []}


def test_memory_list_field_projection_and_status(api):
    client, _, ltm = api
    mid = ltm.add_candidate("life_event", {"summary": "北京"}, {"conversation_id": "SECRET"})
    response = client.get("/api/memory?status=candidate")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert set(item) == {"memory_id", "kind", "status", "content", "created_at", "confirmed_at"}
    assert item["memory_id"] == mid
    assert "source" not in item and "dedup_key" not in item
    assert "SECRET" not in response.text
    assert client.get("/api/memory").json()["items"] == [item]
    assert client.get("/api/memory?status=confirmed").json()["items"] == []
    assert client.get("/api/memory?status=bad").status_code == 400
    assert client.get("/api/memory?learner=other").json()["items"] == []


@pytest.mark.parametrize("action,status", [("confirm", "confirmed"), ("reject", "rejected"), ("forget", "forgotten")])
def test_memory_transition_normal_and_errors(api, action, status):
    client, _, ltm = api
    mid = ltm.add_candidate("life_event", {"summary": "北京"}, {})
    url = "/api/memory/" + action
    response = client.post(url, json={"memory_id": mid, "actor": "operator"})
    assert response.status_code == 200
    assert response.json() == {"memory_id": mid, "status": status}
    assert ltm.get(mid)["last_actor"] == "learner"
    for body in ({}, {"memory_id": "missing"}, {"memory_id": []}, [], {"memory_id": mid, "learner": "other"}):
        error = client.post(url, json=body)
        assert error.status_code == 400
        assert "error" in error.json()
    ltm.forget(mid, "learner")
    if action != "forget":
        assert client.post(url, json={"memory_id": mid}).status_code == 400


def test_profile_memory_enabled_partial_merge_and_validation(api):
    client, mem, _ = api
    assert client.get("/api/profile").json()["profile"]["memory_enabled"] is True
    assert "memory_enabled" not in mem.get_profile()
    mem.update_profile(user_level="HSK4", persona={"address": "朋友"})
    for value in (False, True):
        response = client.post("/api/profile", json={"memory_enabled": value})
        assert response.status_code == 200
        assert response.json()["memory_enabled"] is value
        assert client.get("/api/profile").json()["profile"]["memory_enabled"] is value
        assert mem.get_profile()["user_level"] == "HSK4"
        assert mem.get_profile()["persona"] == {"address": "朋友"}
    for value in (None, 0, 1, "false", [], {}):
        assert client.post("/api/profile", json={"memory_enabled": value}).status_code == 400
    assert mem.get_profile()["memory_enabled"] is True


def test_dialog_recall_notice_reaches_sse_degraded(api):
    client, _, _ = api
    with patch("engine.memory.long_term.LongTermMemory.recall", side_effect=RuntimeError("offline")):
        response = client.post("/api/dialog", json={"text": "你好", "conversation_id": "new"})
    assert response.status_code == 200
    payload = parse_body("/api/dialog", response.text)
    assert {"stage": "memory_recall", "reason": "recall_failed", "fatal": False} in payload["degraded"]
    assert payload["text"] == "好的"


def test_provider_adapter_uses_default_config(tmp_path):
    provider = {"base_url": "https://example.com/v1", "api_key": "fake", "model": "model"}
    with patch("engine.dialog_service.ProviderResolver.resolve", return_value=(provider, None)) as resolve, patch("engine.llm.client.LLMClient.chat_json_strict", return_value={"candidates": []}) as chat:
        client = _memory_client_factory(str(tmp_path))
        assert client.chat_json_strict(system="system", user="user") == {"candidates": []}
        resolve.assert_called_once_with("")
        assert chat.call_args.kwargs == {"system": "system", "user": "user", "config": provider}
    with patch("engine.dialog_service.ProviderResolver.resolve", return_value=(None, (400, {}))):
        assert _memory_client_factory(str(tmp_path)) is None

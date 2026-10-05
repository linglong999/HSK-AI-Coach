from fastapi.testclient import TestClient

from engine.serve import create_app


def test_study_http_lifecycle(tmp_path):
    app = create_app(object(), str(tmp_path), memory_root=str(tmp_path))
    client = TestClient(app, base_url="http://127.0.0.1:8612")

    created = client.post("/api/study/create", json={"scene_type": "survival"})
    assert created.status_code == 200
    study_id = created.json()["study_session_id"]
    assert created.json()["learner_id"].startswith("a1-")

    consented = client.post("/api/study/consent", json={
        "study_session_id": study_id,
        "consent_version": "consent-v1",
    })
    assert consented.status_code == 200

    summary = client.get("/api/study/summary", params={
        "study_session_id": study_id,
    })
    assert summary.status_code == 200
    assert summary.json()["sessions"][0]["status"] == "consented"

    completed = client.post("/api/study/complete", json={
        "study_session_id": study_id,
        "reason": "author baseline finished",
    })
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"

    withdrawn = client.post("/api/study/withdraw", json={
        "study_session_id": study_id,
    })
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "withdrawn"

    deleted = client.post("/api/study/delete", json={
        "study_session_id": study_id,
    })
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get("/api/study/summary").json()["count"] == 0


def test_study_http_rejects_missing_consent_version(tmp_path):
    app = create_app(object(), str(tmp_path), memory_root=str(tmp_path))
    client = TestClient(app, base_url="http://127.0.0.1:8612")
    response = client.post("/api/study/consent", json={"study_session_id": "missing"})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_study_consent"

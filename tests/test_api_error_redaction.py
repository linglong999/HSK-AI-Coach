"""内部异常不得通过本机 API 暴露密钥、路径或供应商响应。"""

from unittest import mock

from fastapi.testclient import TestClient

from engine.dialog_service import DialogService
from engine.serve import create_app


SECRET = "sk-fake-private-key-do-not-return"


def _explode(*args, **kwargs):
    raise RuntimeError(f"backend path C:/private/{SECRET}")


class _Graph:
    get_review_queue = _explode
    review_feedback = _explode


class _Router:
    learner_id = "redaction_test"
    graph = _Graph()


def _client(tmp_path):
    app = create_app(_Router(), str(tmp_path), memory_root=str(tmp_path))
    return TestClient(app, base_url="http://127.0.0.1:8612")


def _check(response, expected_status):
    assert response.status_code == expected_status
    assert SECRET not in response.text
    assert "C:/private" not in response.text


def test_http_fallbacks_redact_internal_exception(tmp_path):
    client = _client(tmp_path)
    with mock.patch.object(DialogService, "generate", _explode):
        _check(client.post("/api/generate", json={}), 500)
    with mock.patch("engine.scenarios.list_scene_cards", _explode):
        _check(client.get("/api/scenarios"), 200)
    with mock.patch("engine.metrics.all_metrics", _explode):
        _check(client.get("/api/metrics"), 500)
    with mock.patch("engine.serve._alignment_summary", _explode):
        _check(client.get("/api/alignment"), 500)
    _check(client.get("/api/quiz"), 500)
    _check(client.post("/api/quiz/answer", json={
        "answers": [{"kp_id": "test", "correct": True}]}), 200)


def test_ocr_error_redacts_dependency_exception(tmp_path):
    from engine.ocr import OcrUnavailable

    client = _client(tmp_path)
    with mock.patch("engine.ocr.extract_text_from_images",
                    side_effect=OcrUnavailable(SECRET)):
        _check(client.post("/api/ocr", json={"data": "AA=="}), 422)


def test_ledger_notice_redacts_internal_exception():
    ledger = mock.Mock()
    ledger.record.side_effect = RuntimeError(SECRET)
    wb = mock.Mock(ledger=ledger)
    notices = DialogService._writeback_ledger_events(wb, [{
        "ok": True, "name": "identify_errors", "result": {"errors": [{
            "knowledge_point_id": "kp", "fragment": "x"}]}}], "input")
    assert notices == [{"stage": "ledger_write", "reason": "ledger_write_failed",
                        "fatal": False}]

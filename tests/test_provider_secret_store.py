"""BYOK 文件保护与旧明文格式迁移；只使用隔离临时目录及虚构 Key。"""

import json
import os
import stat
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from engine import providers
from engine.provider_secret import protect, unprotect


def _sample_store(key="dummy-test-key"):
    return {"providers": [providers.new_provider(
        "Test", "https://8.8.8.8/v1", key, "test-model")], "default_id": None}


def test_roundtrip_and_disk_format():
    with tempfile.TemporaryDirectory() as root:
        store = _sample_store()
        providers.save_store(root, store)
        path = Path(root, "llm_providers.json")
        raw = path.read_text(encoding="utf-8")
        assert providers.load_store(root)["providers"][0]["api_key"] == "dummy-test-key"
        if os.name == "nt":
            assert "dummy-test-key" not in raw
            assert "api_key_protected" in raw
            assert "dpapi-user-v1:" in raw
        else:
            assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_legacy_plaintext_migrates_on_first_read():
    with tempfile.TemporaryDirectory() as root:
        path = Path(root, "llm_providers.json")
        path.write_text(json.dumps(_sample_store()), encoding="utf-8")
        loaded = providers.load_store(root)
        assert loaded["providers"][0]["api_key"] == "dummy-test-key"
        assert "dummy-test-key" not in path.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_migration_failure_preserves_legacy_file():
    with tempfile.TemporaryDirectory() as root:
        path = Path(root, "llm_providers.json")
        original = json.dumps(_sample_store())
        path.write_text(original, encoding="utf-8")
        with mock.patch("engine.provider_secret.protect", side_effect=RuntimeError("dpapi failed")):
            with pytest.raises(RuntimeError, match="dpapi failed"):
                providers.load_store(root)
        assert path.read_text(encoding="utf-8") == original


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_unreadable_protected_key_does_not_become_empty_store():
    with tempfile.TemporaryDirectory() as root:
        path = Path(root, "llm_providers.json")
        providers.save_store(root, _sample_store())
        data = json.loads(path.read_text(encoding="utf-8"))
        data["providers"][0]["api_key_protected"] = "dpapi-user-v1:!!!"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(RuntimeError, match="解密失败"):
            providers.load_store(root)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_dpapi_same_user_roundtrip():
    encrypted = protect("dummy-test-key")
    assert "dummy-test-key" not in encrypted
    assert unprotect(encrypted) == "dummy-test-key"

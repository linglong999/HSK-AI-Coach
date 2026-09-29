"""P1：未完成认证前，CLI 不允许对外监听。"""

import pytest

from engine.serve import _is_loopback_host


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost"])
def test_loopback_allowed(host):
    assert _is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.2", "10.0.0.1", "127.1",
                                   "example.com", "", "127.0.0.1.evil.example"])
def test_non_loopback_rejected(host):
    assert not _is_loopback_host(host)

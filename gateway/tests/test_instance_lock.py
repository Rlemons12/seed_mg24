import pytest

from gateway.app.instance_lock import GatewayInstanceLock


def test_second_gateway_owner_is_rejected_and_release_allows_restart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = GatewayInstanceLock("sqlite:///one.db", 8123)
    second = GatewayInstanceLock("sqlite:///two.db", 8123)
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="Another dashboard instance"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.app.config import Settings
from gateway.app.schemas import Discovery


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        sensor_profile_directory=tmp_path / "profiles",
        scan_duration_seconds=1,
        reconnect_initial_seconds=0.1,
        reconnect_max_seconds=1,
        reconnect_stable_seconds=1,
        poll_interval_seconds=0.2,
    )


@pytest.fixture
def app(settings):
    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            self.is_connected = False

        async def connect(self):
            self.is_connected = True

        async def disconnect(self):
            self.is_connected = False

        async def read_gatt_char(self, _uuid):
            raise RuntimeError("fixture has no metadata")

        async def start_notify(self, *_args):
            return None

        async def write_gatt_char(self, *_args, **_kwargs):
            return None

    return create_app(settings, client_factory=FakeClient)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def compatible_discovery(app):
    discovery = Discovery(
        address="AA:BB:CC:DD:EE:01",
        name="XIAO-MG24-Sense",
        rssi=-48,
        service_uuids=["0100004d-4724-2480-2d4d-47240024beef"],
        compatible=True,
        compatibility_reason="advertises the Seed MG24 telemetry service",
        last_seen_at=datetime.now(UTC),
    )
    app.state.scanner.record(discovery)
    return discovery

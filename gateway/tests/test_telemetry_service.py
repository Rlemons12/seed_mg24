import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from gateway.app.database import create_database_engine, create_session_factory, initialize_database
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.services.telemetry_persistence import PersistenceOutcome
from gateway.app.services.telemetry_service import TelemetryService


class RecordingWebSockets:
    def __init__(self):
        self.events = []

    async def broadcast(self, *args):
        self.events.append(args)


@pytest.mark.asyncio
async def test_deduplication_and_uptime_reset(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        DeviceRepository(session).create(device_id="ARM2001-01", display_name="Node")
    sockets = RecordingWebSockets()
    service = TelemetryService(factory, sockets)
    first = json.dumps(
        {
            "t": "m",
            "v": 1,
            "id": "ARM2001-01",
            "s": 1,
            "ms": 100,
            "c": "sensor_1",
            "rv": 12,
            "u": "adc_count",
            "q": "uncalibrated",
        }
    )
    assert len(await service.ingest("ARM2001-01", first)) == 1
    assert await service.ingest("ARM2001-01", first) == []
    second = json.dumps(
        {
            "t": "m",
            "v": 1,
            "id": "ARM2001-01",
            "s": 2,
            "ms": 5,
            "c": "sensor_1",
            "rv": 13,
            "u": "adc_count",
            "q": "uncalibrated",
        }
    )
    rows = await service.ingest("ARM2001-01", second)
    assert rows[0].session_id != (await _first_session(factory))


async def _first_session(factory):
    from sqlalchemy import select

    from gateway.app.models import Reading

    with factory() as session:
        return session.scalars(select(Reading).order_by(Reading.id)).first().session_id


@pytest.mark.asyncio
async def test_websocket_event_is_serializable(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        DeviceRepository(session).create(device_id="ARM2001-01", display_name="Node")
    sockets = RecordingWebSockets()
    service = TelemetryService(factory, sockets)
    await service.ingest("ARM2001-01", '{"t":"h","v":1,"id":"ARM2001-01","s":9,"ms":20,"bu":0}')
    json.dumps(sockets.events[0][2])


@pytest.mark.asyncio
async def test_high_rate_battery_channel_is_persisted_but_battery_state_writes_are_throttled(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        DeviceRepository(session).create(device_id="ARM2001-01", display_name="Node")

    class RecordingBattery:
        def __init__(self):
            self.calls = 0

        def process_readings(self, _device_id, _rows):
            self.calls += 1

    battery = RecordingBattery()
    service = TelemetryService(factory, RecordingWebSockets(), battery_service=battery, battery_processing_interval_seconds=60)
    await service.ingest("ARM2001-01", '{"t":"h","v":1,"s":1,"ms":10,"bv":4.0,"bu":0,"dr":0,"pe":0,"se":0}')
    await service.ingest("ARM2001-01", '{"t":"h","v":1,"s":2,"ms":20,"bv":3.99,"bu":0,"dr":0,"pe":0,"se":0}')
    assert battery.calls == 1


@pytest.mark.asyncio
async def test_flash_journal_age_restores_measurement_time(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        DeviceRepository(session).create(device_id="ARM2001-01", display_name="Node")
    service = TelemetryService(factory, RecordingWebSockets())
    received = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    packet = (
        '{"t":"tele","v":2,"id":"ARM2001-01","bid":"0123456789abcdef","s":9,"ms":1000,'
        '"sc":5,"bv":3.9,"a":[0,0,1],"g":[0,0,0],"n":[],"d":1,"pj":1,"jm":60000}'
    )
    with patch("gateway.app.ble.telemetry_parser.datetime") as clock:
        clock.now.return_value = received
        rows = await service.ingest("ARM2001-01", packet)
    assert rows[0].delayed is True
    assert rows[0].measured_at == received - timedelta(seconds=60)


@pytest.mark.asyncio
async def test_concurrent_telemetry_database_writes_are_serialized(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    factory = create_session_factory(engine)

    class TrackingPersistence:
        def __init__(self):
            self.active = 0
            self.maximum_active = 0
            self.lock = threading.Lock()

        def persist(self, *_args, **_kwargs):
            with self.lock:
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
            time.sleep(0.05)
            with self.lock:
                self.active -= 1
            return PersistenceOutcome([])

    persistence = TrackingPersistence()
    service = TelemetryService(factory, RecordingWebSockets(), persistence_service=persistence)
    await asyncio.gather(
        service.ingest("ARM2001-01", '{"t":"m","v":1,"s":1,"ms":10,"c":"analog_0","rv":1}'),
        service.ingest("ARM2001-01", '{"t":"m","v":1,"s":2,"ms":20,"c":"analog_0","rv":2}'),
    )
    assert persistence.maximum_active == 1

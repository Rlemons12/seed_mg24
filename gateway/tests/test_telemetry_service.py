import json

import pytest

from gateway.app.database import create_database_engine, create_session_factory, initialize_database
from gateway.app.repositories.device_repository import DeviceRepository
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

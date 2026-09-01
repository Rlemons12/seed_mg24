import json

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from gateway.app.database import create_database_engine, create_session_factory, initialize_database
from gateway.app.models import Reading, TelemetrySyncState
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.services.telemetry_persistence import TelemetryIdentityConflict, TelemetryPersistenceError
from gateway.app.services.telemetry_service import TelemetryService


class NullWebSockets:
    async def broadcast(self, *_args):
        pass


def packet(boot_id: str, sequence: int, value: int = 12) -> str:
    return json.dumps({"t": "m", "v": 2, "id": "ARM2001-01", "bid": boot_id,
                       "s": sequence, "ms": sequence * 100, "c": "analog_0", "rv": value,
                       "u": "adc_count", "q": "uncalibrated"}, separators=(",", ":"))


def setup_service(settings, sender=None):
    engine = create_database_engine(settings)
    gateway_id = initialize_database(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        DeviceRepository(session).create(device_id="ARM2001-01", display_name="Node")
    return engine, factory, TelemetryService(factory, NullWebSockets(), gateway_id=gateway_id,
                                              acknowledgement_sender=sender)


@pytest.mark.asyncio
async def test_commit_then_ack_duplicate_and_lost_ack_recovery(settings):
    acknowledgements = []
    lose_first = True

    async def sender(device_id, boot_id, sequence):
        nonlocal lose_first
        if lose_first:
            lose_first = False
            raise ConnectionError("simulated lost ACK")
        acknowledgements.append((device_id, boot_id, sequence))

    _engine, factory, service = setup_service(settings, sender)
    boot = "0123456789abcdef"
    with pytest.raises(ConnectionError, match="lost ACK"):
        await service.ingest("ARM2001-01", packet(boot, 42))
    assert await service.ingest("ARM2001-01", packet(boot, 42)) == []
    assert acknowledgements == [("ARM2001-01", boot, 42)]
    with factory() as session:
        assert len(list(session.scalars(select(Reading)))) == 1
        state = session.scalar(select(TelemetrySyncState))
    assert state.highest_contiguous_sequence == 42 and state.duplicate_count == 1


@pytest.mark.asyncio
async def test_gap_out_of_order_fill_gateway_restart_and_sensor_reboot(settings):
    acknowledgements = []

    async def sender(_device_id, boot_id, sequence):
        acknowledgements.append((boot_id, sequence))

    _engine, factory, service = setup_service(settings, sender)
    boot_a, boot_b = "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"
    await service.ingest("ARM2001-01", packet(boot_a, 100))
    await service.ingest("ARM2001-01", packet(boot_a, 101))
    await service.ingest("ARM2001-01", packet(boot_a, 103))
    # Sequence 102 may have been irrecoverably dropped by the sensor's bounded
    # buffer. The persisted head (103) must be ACKed so telemetry can continue.
    assert acknowledgements[-1] == (boot_a, 103)
    with factory() as session:
        state = session.scalar(select(TelemetrySyncState).where(TelemetrySyncState.sensor_boot_id == boot_a))
    assert state.highest_seen_sequence == 103 and state.missing_sequence_count == 1
    restarted = TelemetryService(factory, NullWebSockets(), acknowledgement_sender=sender)
    assert await restarted.ingest("ARM2001-01", packet(boot_a, 101)) == []
    await restarted.ingest("ARM2001-01", packet(boot_a, 102))
    assert acknowledgements[-1] == (boot_a, 103)
    await restarted.ingest("ARM2001-01", packet(boot_b, 1))
    assert acknowledgements[-1] == (boot_b, 1)
    with factory() as session:
        assert len(list(session.scalars(select(TelemetrySyncState)))) == 2


@pytest.mark.asyncio
async def test_conflicting_duplicate_is_rejected_without_ack(settings):
    acknowledgements = []

    async def sender(*args):
        acknowledgements.append(args)

    _engine, factory, service = setup_service(settings, sender)
    boot = "0123456789abcdef"
    await service.ingest("ARM2001-01", packet(boot, 7, 12))
    with pytest.raises(TelemetryIdentityConflict):
        await service.ingest("ARM2001-01", packet(boot, 7, 99))
    assert len(acknowledgements) == 1
    with factory() as session:
        state = session.scalar(select(TelemetrySyncState))
        stored = session.scalar(select(Reading))
    assert state.conflict_count == 1 and stored.raw_value == 12


@pytest.mark.asyncio
async def test_delayed_replay_marker_does_not_conflict_with_persisted_packet(settings):
    acknowledgements = []

    async def sender(*args):
        acknowledgements.append(args)

    _engine, factory, service = setup_service(settings, sender)
    boot = "0123456789abcdef"
    original = packet(boot, 8, 12)
    replay = json.loads(original)
    replay["d"] = 1
    await service.ingest("ARM2001-01", original)
    assert await service.ingest(
        "ARM2001-01", json.dumps(replay, separators=(",", ":"))
    ) == []
    with factory() as session:
        rows = list(session.scalars(select(Reading)))
        state = session.scalar(select(TelemetrySyncState))
    assert len(rows) == 1
    assert state.duplicate_count == 1 and state.conflict_count == 0
    assert acknowledgements[-1] == ("ARM2001-01", boot, 8)


@pytest.mark.asyncio
async def test_transaction_failure_sends_no_ack(settings):
    acknowledgements = []

    async def sender(*args):
        acknowledgements.append(args)

    engine, _factory, service = setup_service(settings, sender)

    def fail_insert(_connection, _cursor, statement, parameters, _context, _many):
        if statement.lstrip().startswith("INSERT INTO readings"):
            raise OperationalError(statement, parameters, RuntimeError("simulated failure"))

    event.listen(engine, "before_cursor_execute", fail_insert)
    with pytest.raises(TelemetryPersistenceError):
        await service.ingest("ARM2001-01", packet("0123456789abcdef", 1))
    event.remove(engine, "before_cursor_execute", fail_insert)
    assert acknowledgements == []


@pytest.mark.asyncio
async def test_legacy_packet_remains_supported_and_is_not_acknowledged(settings):
    acknowledgements = []

    async def sender(*args):
        acknowledgements.append(args)

    _engine, _factory, service = setup_service(settings, sender)
    rows = await service.ingest("ARM2001-01", '{"t":"m","v":1,"id":"ARM2001-01","s":1,"ms":10,"c":"analog_0","rv":3}')
    assert len(rows) == 1 and rows[0].sensor_boot_id is None and acknowledgements == []

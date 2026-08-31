import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from gateway.app.config import Settings
from gateway.app.database import checkpoint_sqlite, create_database_engine, create_session_factory, initialize_database
from gateway.app.models import Reading, RegisteredDevice, SensorInstallation, VibrationWindow
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.services.telemetry_persistence import TelemetryPersistenceError
from gateway.app.services.telemetry_retention import TelemetryRetentionService
from gateway.app.services.telemetry_service import TelemetryService


class RecordingWebSockets:
    def __init__(self):
        self.events = []

    async def broadcast(self, *args):
        self.events.append(args)


def add_node(factory, node_id="ARM2001-01"):
    with factory() as session:
        return DeviceRepository(session).create(device_id=node_id, display_name="Node")


def reading(device_id, received_at, **values):
    return Reading(
        registered_device_id=device_id,
        received_at=received_at,
        session_id=values.pop("session_id", "boot-1"),
        channel=values.pop("channel", "microphone_raw"),
        payload_json=values.pop("payload_json", "{}"),
        **values,
    )


def test_fresh_sqlite_initialization_sets_identity_pragmas_and_indexes(settings):
    engine = create_database_engine(settings)
    gateway_id = initialize_database(engine)

    assert len(gateway_id) == 36
    assert {"gateway_identity", "readings", "registered_devices", "sensor_installations"} <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
        assert connection.scalar(text("PRAGMA synchronous")) == 1
        assert connection.scalar(text("PRAGMA busy_timeout")) == 5000
    indexes = {item["name"] for item in inspect(engine).get_indexes("readings")}
    assert {"ix_readings_device_received", "ix_readings_installation_channel_received", "ix_readings_channel_received"} <= indexes


def test_retention_blank_is_disabled_and_gateway_identity_cannot_silently_change(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'identity.db'}",
        sensor_profile_directory=tmp_path / "profiles",
        history_retention_days="",
        gateway_instance_lock=False,
    )
    assert settings.history_retention_days is None
    engine = create_database_engine(settings)
    first = initialize_database(engine, "11111111-1111-1111-1111-111111111111")
    assert first == "11111111-1111-1111-1111-111111111111"
    with pytest.raises(ValueError, match="does not match"):
        initialize_database(engine, "22222222-2222-2222-2222-222222222222")


def test_existing_readings_are_migrated_backfilled_and_preserved(tmp_path):
    path = tmp_path / "existing.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE registered_devices (id INTEGER PRIMARY KEY, device_id VARCHAR(96))"))
        connection.execute(text("INSERT INTO registered_devices VALUES (1, 'ARM2001-01')"))
        connection.execute(
            text("""
            CREATE TABLE readings (
              id INTEGER PRIMARY KEY, registered_device_id INTEGER NOT NULL, received_at DATETIME NOT NULL,
              session_id VARCHAR(64) NOT NULL, channel VARCHAR(96) NOT NULL, payload_json TEXT NOT NULL
            )
        """)
        )
        connection.execute(
            text("INSERT INTO readings VALUES (7, 1, '2026-08-01 12:00:00', 'boot-old', 'analog_0', :payload)"),
            {"payload": '{"v":1}'},
        )

    gateway_id = initialize_database(engine)
    initialize_database(engine)

    columns = {item["name"] for item in inspect(engine).get_columns("readings")}
    assert {"gateway_id", "reading_uuid", "measured_at", "installation_id", "interface_id"} <= columns
    assert {"vibration_windows", "vibration_baselines", "vibration_conditions"} <= set(
        inspect(engine).get_table_names()
    )
    with engine.connect() as connection:
        row = connection.execute(text("SELECT id, channel, gateway_id, reading_uuid FROM readings")).one()
    assert row.id == 7 and row.channel == "analog_0" and row.gateway_id == gateway_id and len(row.reading_uuid) == 36


@pytest.mark.asyncio
async def test_packet_persists_multiple_channels_with_node_installation_interface_and_utc(settings):
    engine = create_database_engine(settings)
    gateway_id = initialize_database(engine)
    factory = create_session_factory(engine)
    node = add_node(factory)
    with factory() as session:
        session.add(
            SensorInstallation(
                installation_id="installation-mic",
                node_id="ARM2001-01",
                device_id="MIC-0001",
                display_name="Mic",
                sensor_profile_id="seeed-xiao-mg24-microphone",
                sensor_profile_version="1.0.0",
                interface_id="MIC",
                enabled=True,
                provisioning_state="active",
            )
        )
        session.commit()
    service = TelemetryService(factory, RecordingWebSockets(), gateway_id=gateway_id)
    rows = await service.ingest(
        "ARM2001-01",
        json.dumps({"t": "telemetry", "id": "ARM2001-01", "s": 8, "ms": 2500, "m": 12, "mp": 5, "bv": 4.0}),
    )

    assert len(rows) == 3
    with factory() as session:
        stored = list(session.scalars(select(Reading).order_by(Reading.channel)))
        device = session.get(RegisteredDevice, node.id)
    assert len(stored) == 3 and {item.gateway_id for item in stored} == {gateway_id}
    assert all(item.reading_uuid and item.received_at for item in stored)
    assert all(item.received_at.tzinfo is not None for item in rows)
    assert {(item.channel, item.interface_id, item.installation_id) for item in stored} >= {
        ("microphone_raw", "MIC", "installation-mic"),
        ("battery_voltage", "VBAT", None),
    }
    assert device.last_seen_at is not None
    # The parser owns receive time; SQLite may return it without tzinfo, while the API restores UTC explicitly.
    assert all(item.measured_at is not None for item in stored)


def test_history_filters_cursor_bounds_and_utc_response(client, app, compatible_discovery):
    client.post(
        "/api/devices",
        json={"device_id": "ARM2001-01", "display_name": "Node", "discovery_address": compatible_discovery.address},
    )
    now = datetime.now(UTC)
    with app.state.session_factory() as session:
        node = session.scalar(select(RegisteredDevice).where(RegisteredDevice.device_id == "ARM2001-01"))
        session.add_all(
            [
                reading(node.id, now - timedelta(seconds=3), channel="analog_0", installation_id="i-1", interface_id="D0"),
                reading(node.id, now - timedelta(seconds=2), channel="analog_0", installation_id="i-1", interface_id="D0"),
                reading(node.id, now - timedelta(seconds=1), channel="battery_voltage", interface_id="VBAT"),
            ]
        )
        session.commit()

    first = client.get("/api/devices/ARM2001-01/readings?limit=1&installation_id=i-1&include_total=false")
    assert first.status_code == 200
    body = first.json()
    assert body["total"] is None and body["next_cursor"] and body["items"][0]["installation_id"] == "i-1"
    assert body["items"][0]["received_at"].endswith("Z")
    second = client.get(
        "/api/devices/ARM2001-01/readings",
        params={"limit": 1, "installation_id": "i-1", "include_total": "false", "cursor": body["next_cursor"]},
    )
    assert second.status_code == 200 and second.json()["items"][0]["id"] != body["items"][0]["id"]
    assert client.get("/api/devices/ARM2001-01/readings?cursor=not-valid").status_code == 422


@pytest.mark.asyncio
async def test_missing_optional_uptime_leaves_measurement_time_null(settings):
    engine = create_database_engine(settings)
    gateway_id = initialize_database(engine)
    factory = create_session_factory(engine)
    add_node(factory)
    service = TelemetryService(factory, RecordingWebSockets(), gateway_id=gateway_id)

    rows = await service.ingest("ARM2001-01", '{"t":"m","v":1,"id":"ARM2001-01","c":"analog_0","rv":3}')

    assert rows[0].device_uptime_ms is None and rows[0].measured_at is None and rows[0].sequence_number is None


@pytest.mark.asyncio
async def test_failed_write_rolls_back_and_same_packet_can_be_retried(settings):
    engine = create_database_engine(settings)
    gateway_id = initialize_database(engine)
    factory = create_session_factory(engine)
    add_node(factory)
    service = TelemetryService(factory, RecordingWebSockets(), gateway_id=gateway_id)
    packet = '{"t":"m","v":1,"id":"ARM2001-01","s":5,"ms":10,"c":"analog_0","rv":3}'

    def fail_reading_insert(_connection, _cursor, statement, parameters, _context, _many):
        if statement.lstrip().startswith("INSERT INTO readings"):
            raise OperationalError(statement, parameters, RuntimeError("simulated write failure"))

    event.listen(engine, "before_cursor_execute", fail_reading_insert)

    with pytest.raises(TelemetryPersistenceError):
        await service.ingest("ARM2001-01", packet)
    event.remove(engine, "before_cursor_execute", fail_reading_insert)
    assert len(await service.ingest("ARM2001-01", packet)) == 1
    with factory() as session:
        assert len(list(session.scalars(select(Reading)))) == 1


def test_retention_disabled_enabled_boundary_and_batching(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    factory = create_session_factory(engine)
    node = add_node(factory)
    now = datetime(2026, 8, 10, tzinfo=UTC)
    with factory() as session:
        session.add_all(
            [
                reading(node.id, now - timedelta(days=31), channel="old-1"),
                reading(node.id, now - timedelta(days=31, seconds=1), channel="old-2"),
                reading(node.id, now - timedelta(days=30), channel="boundary"),
                reading(node.id, now - timedelta(days=1), channel="new"),
            ]
        )
        session.commit()

    assert TelemetryRetentionService(factory, None, 100).cleanup_batch(now) == 0
    assert TelemetryRetentionService(factory, 30, 1).cleanup_batch(now) == 1
    assert TelemetryRetentionService(factory, 30, 100).cleanup_batch(now) == 1
    with factory() as session:
        assert {item.channel for item in session.scalars(select(Reading))} == {"boundary", "new"}


def test_retention_deletes_vibration_and_reading_batches_together(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    factory = create_session_factory(engine)
    node = add_node(factory)
    now = datetime(2026, 8, 10, tzinfo=UTC)
    metrics = {
        "effective_sample_rate_hz": 416.0, "validity": "valid",
        **{name: 0.0 for name in (
            "accel_rms_x_g", "accel_rms_y_g", "accel_rms_z_g", "accel_peak_x_g", "accel_peak_y_g",
            "accel_peak_z_g", "crest_x", "crest_y", "crest_z", "kurtosis_x", "kurtosis_y", "kurtosis_z",
            "dominant_frequency_x_hz", "dominant_frequency_y_hz", "dominant_frequency_z_hz",
            "dominant_amplitude_x_g", "dominant_amplitude_y_g", "dominant_amplitude_z_g",
            "gyro_rms_x_dps", "gyro_rms_y_dps", "gyro_rms_z_dps",
        )},
    }
    with factory() as session:
        session.add(reading(node.id, now - timedelta(days=2), channel="old"))
        session.add(VibrationWindow(
            gateway_id="00000000-0000-0000-0000-000000000000", registered_device_id=node.id,
            session_id="old", window_sequence=1, observed_at=now - timedelta(days=2),
            device_uptime_ms=1, schema_version=1, algorithm_version=1, **metrics,
        ))
        session.commit()
    assert TelemetryRetentionService(factory, 1, 100).cleanup_batch(now) == 2
    with factory() as session:
        assert session.scalar(select(Reading.id)) is None
        assert session.scalar(select(VibrationWindow.id)) is None


def test_passive_sqlite_checkpoint_is_bounded_and_validated(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    result = checkpoint_sqlite(engine)
    assert result is not None and len(result) == 3 and all(item >= 0 for item in result)
    with pytest.raises(ValueError, match="unsupported SQLite checkpoint mode"):
        checkpoint_sqlite(engine, "DELETE EVERYTHING")


def test_database_reopen_preserves_gateway_identity_and_readings(tmp_path):
    path = tmp_path / "restart.db"
    engine = create_engine(f"sqlite:///{path}")
    gateway_id = initialize_database(engine)
    factory = create_session_factory(engine)
    node = add_node(factory)
    with factory() as session:
        session.add(reading(node.id, datetime.now(UTC)))
        session.commit()
    engine.dispose()

    reopened = create_engine(f"sqlite:///{path}")
    assert initialize_database(reopened) == gateway_id
    with reopened.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM readings")) == 1


def test_sqlite_foreign_key_restricts_deleting_node_with_history(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    factory = create_session_factory(engine)
    node = add_node(factory)
    with factory() as session:
        session.add(reading(node.id, datetime.now(UTC)))
        session.commit()
        with pytest.raises(IntegrityError):
            session.delete(session.get(RegisteredDevice, node.id))
            session.commit()
        session.rollback()

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select

from gateway.app.ble.telemetry_parser import TelemetryParseError, parse_telemetry
from gateway.app.database import create_database_engine, create_session_factory, initialize_database
from gateway.app.models import VibrationBaseline, VibrationBaselineHistory, VibrationCondition, VibrationWindow
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.schemas import VibrationSummary
from gateway.app.services.telemetry_service import TelemetryService
from gateway.app.services.vibration_condition import (
    VibrationConditionService,
    evaluate_condition,
    summary_values,
    update_statistics,
)

PAYLOAD = {
    "t": "v", "v": 1, "s": 1, "m": 1000, "a": 1, "f": 4317, "q": 1,
    "r": [10, 20, 30], "p": [30, 50, 70], "c": [30, 25, 23],
    "k": [30, 31, 32], "d": [298, 300, 301], "x": [4, 6, 18],
    "g": [2, 2, 1],
}


class RecordingWebSockets:
    def __init__(self):
        self.events = []

    async def broadcast(self, *args):
        self.events.append(args)


def test_vibration_wire_payload_worst_case_fits_single_notification():
    worst = {
        "t": "v", "v": 1, "s": 0xFFFFFFFF, "m": 0xFFFFFFFF,
        "a": 1, "f": 5000, "q": 1,
        "r": [16000] * 3, "p": [16000] * 3, "c": [1000] * 3,
        "k": [10000] * 3, "d": [2500] * 3, "x": [16000] * 3,
        "g": [20000] * 3,
    }
    assert len(json.dumps(worst, separators=(",", ":")).encode()) == 228
    assert len(json.dumps(worst, separators=(",", ":")).encode()) <= 243


def summary(sequence=1, rms=0.01, validity="valid") -> VibrationSummary:
    return VibrationSummary(
        window_sequence=sequence, device_uptime_ms=sequence * 600, effective_sample_rate_hz=431.672,
        validity=validity, accel_rms_g=(rms, rms, rms), accel_peak_g=(rms * 3, rms * 3, rms * 3),
        crest_factor=(3, 3, 3), kurtosis=(3, 3, 3), dominant_frequency_hz=(30, 30, 30),
        dominant_amplitude_g=(rms, rms, rms), gyro_rms_dps=(0.2, 0.2, 0.2),
    )


def setup_service(settings, **kwargs):
    engine = create_database_engine(settings)
    gateway_id = initialize_database(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        DeviceRepository(session).create(device_id="NODE-1", display_name="Node")
    return engine, factory, VibrationConditionService(factory, gateway_id, **kwargs)


def test_vibration_parser_validates_bounded_schema_and_old_telemetry_still_works():
    parsed = parse_telemetry(json.dumps(PAYLOAD))
    assert parsed.record_type == "vibration"
    assert parsed.vibration.accel_rms_g == (0.01, 0.02, 0.03)
    assert parsed.vibration.gyro_rms_dps == (0.2, 0.2, 0.1)
    assert parse_telemetry('{"t":"tele","v":1,"a":[0,0,1]}').record_type == "measurement"
    malformed = {**PAYLOAD, "extra": 1}
    with pytest.raises(TelemetryParseError, match="unexpected"):
        parse_telemetry(json.dumps(malformed))
    malformed = {**PAYLOAD, "r": [1, 2]}
    with pytest.raises(TelemetryParseError, match="length"):
        parse_telemetry(json.dumps(malformed))


def test_fresh_database_has_structured_vibration_tables_and_indexes(settings):
    engine, _factory, _service = setup_service(settings)
    expected = {"vibration_windows", "vibration_baselines", "vibration_baseline_history", "vibration_conditions"}
    assert expected <= set(inspect(engine).get_table_names())
    indexes = {item["name"] for item in inspect(engine).get_indexes("vibration_windows")}
    assert "ix_vibration_device_observed" in indexes


@pytest.mark.asyncio
async def test_telemetry_service_routes_vibration_without_changing_normal_readings(settings):
    _engine, factory, vibration = setup_service(
        settings, minimum_windows=2, persistence_interval_seconds=0.5,
    )
    websockets = RecordingWebSockets()
    service = TelemetryService(factory, websockets, vibration_service=vibration)
    assert await service.ingest("NODE-1", json.dumps(PAYLOAD)) == []
    assert service.vibration_counters["received"] == 1
    assert service.vibration_counters["baseline_eligible"] == 1
    assert service.vibration_counters["database_writes"] == 1
    assert websockets.events[-1][2]["record_type"] == "vibration"
    assert await service.ingest("NODE-1", json.dumps(PAYLOAD)) == []
    assert service.vibration_counters["duplicates"] == 1


def test_baseline_builds_freezes_excludes_invalid_deduplicates_and_persists_restart(settings):
    _engine, factory, service = setup_service(settings, minimum_windows=3, persistence_windows=2,
                                               persistence_interval_seconds=0.5)
    now = datetime.now(UTC) - timedelta(seconds=2)
    assert service.process("NODE-1", summary(1), session_id="boot", observed_at=now)["state"] == "BASELINE_PENDING"
    assert service.process("NODE-1", summary(2, validity="invalid"), session_id="boot",
                           observed_at=now + timedelta(seconds=0.6))["state"] == "INVALID"
    assert service.process("NODE-1", summary(3), session_id="boot",
                           observed_at=now + timedelta(seconds=1.2))["state"] == "BASELINE_PENDING"
    established = service.process("NODE-1", summary(4), session_id="boot", observed_at=now + timedelta(seconds=1.8))
    assert established["baseline_status"] == "frozen" and established["baseline_count"] == 3
    duplicate = service.process("NODE-1", summary(4), session_id="boot", observed_at=now + timedelta(seconds=2.0))
    assert duplicate["duplicate"] is True
    with factory() as session:
        baseline = session.scalar(select(VibrationBaseline))
        assert baseline.sample_count == 3 and baseline.status == "frozen"
        assert len(list(session.scalars(select(VibrationWindow)))) == 4
    restarted = VibrationConditionService(factory, service.gateway_id, minimum_windows=3, persistence_windows=2)
    assert restarted.process("NODE-1", summary(4), session_id="boot", observed_at=now + timedelta(seconds=3))["duplicate"]


def test_condition_hysteresis_factors_recovery_and_relearn(settings):
    _engine, factory, service = setup_service(settings, minimum_windows=3, persistence_windows=2)
    now = datetime.now(UTC)
    for sequence in range(1, 4):
        service.process("NODE-1", summary(sequence), session_id="boot", observed_at=now + timedelta(seconds=sequence))
    first = service.process("NODE-1", summary(4, rms=0.08), session_id="boot", observed_at=now + timedelta(seconds=4))
    assert first["state"] == "NORMAL"  # one noisy window does not transition
    second = service.process("NODE-1", summary(5, rms=0.08), session_id="boot", observed_at=now + timedelta(seconds=5))
    assert second["state"] == "SIGNIFICANT_CHANGE" and second["factors"]
    service.process("NODE-1", summary(6), session_id="boot", observed_at=now + timedelta(seconds=6))
    recovered = service.process("NODE-1", summary(7), session_id="boot", observed_at=now + timedelta(seconds=7))
    assert recovered["state"] == "NORMAL"
    with factory() as session:
        window_count = len(list(session.scalars(select(VibrationWindow))))
    result = service.relearn_baseline("NODE-1", reason="Sensor remounted", request_id="request-0001")
    assert result["status"] == "building" and result["baseline_version"] == 2
    assert result["sample_count"] == 0 and result["condition_state"] == "BASELINE_PENDING"
    with factory() as session:
        active = session.scalar(select(VibrationBaseline))
        old = session.scalar(select(VibrationBaselineHistory))
        condition = session.scalar(select(VibrationCondition))
        assert active.baseline_version == 2 and active.status == "building" and active.sample_count == 0
        assert active.reason == "Sensor remounted"
        assert old.baseline_version == 1 and old.status == "superseded" and old.sample_count == 3
        assert old.reason == "Sensor remounted" and old.superseded_at is not None
        assert condition.state == "BASELINE_PENDING" and condition.baseline_id == active.id
        assert len(list(session.scalars(select(VibrationWindow)))) == window_count
    duplicate = service.relearn_baseline("NODE-1", reason="ignored", request_id="request-0001")
    assert duplicate["duplicate"] is True and duplicate["baseline_version"] == 2
    restarted = service.relearn_baseline("NODE-1", reason="Restart learning", request_id="request-0002")
    assert restarted["baseline_version"] == 3
    with factory() as session:
        assert len(list(session.scalars(select(VibrationBaselineHistory)))) == 2


def test_condition_explains_impulsiveness_and_frequency_shift_in_bin_units():
    normal = summary(1)
    statistics = {}
    for _ in range(20):
        update_statistics(statistics, summary_values(normal))
    changed = summary_values(normal)
    changed["crest_z"] = 8.0
    changed["kurtosis_z"] = 12.0
    changed["dominant_frequency_z_hz"] = 36.0
    state, score, factors = evaluate_condition(changed, statistics, 431.7 / 256)
    assert state == "SIGNIFICANT_CHANGE" and score < 100
    assert {item["feature"] for item in factors} >= {
        "crest_z", "kurtosis_z", "dominant_frequency_z_hz",
    }


def test_vibration_api_relearn_preserves_history_and_reset_alias(client, app, compatible_discovery):
    client.post("/api/devices", json={"device_id": "NODE-1", "display_name": "Node", "discovery_address": compatible_discovery.address})
    service = VibrationConditionService(app.state.session_factory, app.state.gateway_id, minimum_windows=2,
                                        persistence_interval_seconds=0.5)
    now = datetime.now(UTC) - timedelta(seconds=2)
    service.process("NODE-1", summary(1), session_id="boot", observed_at=now)
    service.process("NODE-1", summary(2), session_id="boot", observed_at=now + timedelta(seconds=1))
    latest = client.get("/api/devices/NODE-1/vibration/latest")
    assert latest.status_code == 200 and latest.json()["stale"] is False
    assert latest.json()["observed_at"].endswith("Z")
    assert latest.json()["age_seconds"] >= 0
    assert len(client.get("/api/devices/NODE-1/vibration/history").json()["items"]) == 2
    assert client.get("/api/devices/NODE-1/vibration/baseline").json()["status"] == "frozen"
    assert client.get("/api/devices/NODE-1/condition").json()["state"] == "NORMAL"
    response = client.post("/api/devices/NODE-1/vibration/baseline/relearn", json={
        "confirmation": "RELEARN BASELINE", "reason": "Maintenance completed", "request_id": "request-1000",
    })
    assert response.status_code == 200
    assert response.json()["baseline_version"] == 2 and response.json()["condition_state"] == "BASELINE_PENDING"
    assert client.get("/api/devices/NODE-1/vibration/baseline").json()["sample_count"] == 0
    history = client.get("/api/devices/NODE-1/vibration/baseline/history").json()["items"]
    assert [item["baseline_version"] for item in history] == [2, 1]
    assert history[1]["status"] == "superseded" and history[1]["reason"] == "Maintenance completed"
    assert len(client.get("/api/devices/NODE-1/vibration/history").json()["items"]) == 2
    assert client.get("/api/devices/NODE-1/vibration/history").json()["items"][0]["baseline_version"] == 1
    duplicate = client.post("/api/devices/NODE-1/vibration/baseline/relearn", json={
        "confirmation": "RELEARN BASELINE", "reason": "Maintenance completed", "request_id": "request-1000",
    })
    assert duplicate.json()["duplicate"] is True and duplicate.json()["baseline_version"] == 2
    assert client.post("/api/devices/NODE-1/vibration/baseline/relearn", json={"confirmation": "wrong"}).status_code == 422
    assert client.post("/api/devices/NODE-1/vibration/baseline/reset", json={"confirmation": "wrong"}).status_code == 422
    assert client.post("/api/devices/NODE-1/vibration/baseline/reset", json={"confirmation": "RESET BASELINE"}).status_code == 204


def test_vibration_history_accepts_valid_utc_range_and_rejects_reversed_or_equal(client, compatible_discovery):
    client.post("/api/devices", json={
        "device_id": "NODE-1", "display_name": "Node", "discovery_address": compatible_discovery.address,
    })
    valid = {"start": "2026-08-12T18:18:00Z", "end": "2026-08-12T18:33:00Z", "limit": 500}
    assert client.get("/api/devices/NODE-1/vibration/history", params=valid).status_code == 200
    reversed_range = {**valid, "start": valid["end"], "end": valid["start"]}
    assert client.get("/api/devices/NODE-1/vibration/history", params=reversed_range).status_code == 422
    assert client.get("/api/devices/NODE-1/vibration/history", params={**valid, "start": valid["end"]}).status_code == 422

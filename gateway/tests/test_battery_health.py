from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select

from gateway.app.config import Settings
from gateway.app.models import (
    BatteryAlert,
    BatteryCycle,
    BatteryGeneration,
    BatteryReplacementEvent,
    Reading,
    RegisteredDevice,
)
from gateway.app.services.battery_health import BatteryHealthService

START = datetime(2026, 1, 1, tzinfo=UTC)


def add_device(app, node_id="BAT-0001"):
    with app.state.session_factory() as session:
        session.add(RegisteredDevice(device_id=node_id, display_name="Synthetic battery fixture"))
        session.commit()


def reading(at, voltage, *, boot_id="boot-a"):
    return Reading(
        registered_device_id=1, received_at=at, session_id=f"sensor:{boot_id}", record_type="heartbeat",
        channel="battery_voltage", normalized_value=voltage, unit="V", quality="good", payload_json="{}",
        sensor_boot_id=boot_id,
    )


def detector_service(app):
    settings = app.state.settings.model_copy(update={
        "battery_minimum_voltage_rise": 0.10,
        "battery_voltage_noise_floor": 0.01,
        "battery_charge_confirmation_seconds": 20,
        "battery_charge_minimum_samples": 3,
        "battery_stable_voltage_seconds": 20,
        "battery_maximum_sample_gap_seconds": 120,
        "battery_baseline_minimum_cycles": 3,
    })
    return BatteryHealthService(app.state.session_factory, settings)


def test_noise_and_one_spike_do_not_create_a_new_cycle(app):
    add_device(app)
    service = detector_service(app)
    for offset, voltage in [(0, 3.70), (10, 3.705), (20, 3.80), (30, 3.70), (40, 3.695)]:
        service.process_readings("BAT-0001", [reading(START + timedelta(seconds=offset), voltage)])
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(BatteryCycle)) == 1


def test_sustained_rise_and_stability_confirm_charge(app):
    add_device(app)
    service = detector_service(app)
    samples = [(0, 3.50), (10, 3.55), (25, 3.62), (35, 3.72), (45, 3.721), (70, 3.72)]
    for offset, voltage in samples:
        service.process_readings("BAT-0001", [reading(START + timedelta(seconds=offset), voltage)])
    with app.state.session_factory() as session:
        cycles = list(session.scalars(select(BatteryCycle).order_by(BatteryCycle.cycle_number)))
        assert len(cycles) == 2
        assert cycles[0].is_complete is True
        assert cycles[0].end_reason == "CHARGE_CONFIRMED"
        assert cycles[1].start_reason == "AUTO_CHARGE"


def test_sensor_reboot_does_not_start_cycle(app):
    add_device(app)
    service = detector_service(app)
    service.process_readings("BAT-0001", [reading(START, 3.7, boot_id="boot-a")])
    service.process_readings("BAT-0001", [reading(START + timedelta(seconds=10), 3.69, boot_id="boot-b")])
    with app.state.session_factory() as session:
        cycle = session.scalar(select(BatteryCycle))
        assert cycle.sensor_reboot_count == 1
        assert cycle.is_complete is False


def test_large_gateway_gap_lowers_cycle_observability_and_excludes_baseline(app):
    add_device(app)
    service = detector_service(app)
    service.process_readings("BAT-0001", [reading(START, 3.7)])
    service.process_readings("BAT-0001", [reading(START + timedelta(hours=2), 3.68)])
    service.mark_charged("BAT-0001", occurred_at=START + timedelta(hours=3), voltage=3.9)
    with app.state.session_factory() as session:
        completed = session.scalar(select(BatteryCycle).where(BatteryCycle.is_complete.is_(True)))
        assert completed.is_baseline_eligible is False
        assert completed.exclusion_reason == "LOW_OBSERVABILITY"


def test_battery_alerts_are_deduplicated_within_cooldown(app):
    add_device(app)
    service = detector_service(app)
    service.process_readings("BAT-0001", [reading(START, 3.7)])
    service.process_readings("BAT-0001", [reading(START + timedelta(seconds=10), 3.69)])
    with app.state.session_factory() as session:
        alerts = list(session.scalars(select(BatteryAlert)))
        assert [item.alert_type for item in alerts] == ["BATTERY_DATA_INSUFFICIENT"]


def test_manual_charge_and_partial_charge_classification_survive_restart(app):
    add_device(app)
    service = detector_service(app)
    service.process_readings("BAT-0001", [reading(START, 3.6)])
    service.mark_charged("BAT-0001", occurred_at=START + timedelta(days=2), voltage=3.9, partial_charge=True)
    restarted = detector_service(app)
    summary = restarted.summary("BAT-0001", START + timedelta(days=3))
    cycles = restarted.cycles("BAT-0001")
    assert summary["current_cycle"]["runtime_seconds"] == 86400
    assert cycles[1]["exclusion_reason"] == "PARTIAL_CHARGE"


def test_replacement_preserves_history_and_starts_independent_generation(app):
    add_device(app)
    service = detector_service(app)
    service.process_readings("BAT-0001", [reading(START, 3.6)])
    event = service.replace("BAT-0001", reason="scheduled maintenance", occurred_at=START + timedelta(days=2))
    with app.state.session_factory() as session:
        generations = list(session.scalars(select(BatteryGeneration).order_by(BatteryGeneration.generation_number)))
        assert [item.generation_number for item in generations] == [1, 2]
        assert generations[0].ended_at is not None
        assert session.get(BatteryReplacementEvent, event.id) is not None
    assert service.summary("BAT-0001")["battery_generation"] == 2
    assert service.summary("BAT-0001")["health"]["status"] == "LEARNING"


def seed_completed_cycles(app, runtimes, *, eligible=True, observed_ratio=1.0):
    add_device(app)
    with app.state.session_factory() as session:
        device = session.scalar(select(RegisteredDevice))
        generation = BatteryGeneration(
            registered_device_id=device.id, generation_number=1, started_at=START, start_reason="TEST_FIXTURE",
        )
        session.add(generation)
        session.flush()
        cursor = START
        for number, runtime in enumerate(runtimes, 1):
            ended = cursor + timedelta(seconds=runtime)
            session.add(BatteryCycle(
                registered_device_id=device.id, battery_generation_id=generation.id, cycle_number=number,
                started_at=cursor, ended_at=ended, runtime_seconds=runtime,
                observed_operating_seconds=runtime * observed_ratio, unobserved_seconds=runtime * (1 - observed_ratio),
                start_reason="TEST_FIXTURE", end_reason="TEST_FIXTURE", charge_detection_confidence="HIGH",
                is_complete=True, is_baseline_eligible=eligible, observability_ratio=observed_ratio,
            ))
            cursor = ended
        session.add(BatteryCycle(
            registered_device_id=device.id, battery_generation_id=generation.id, cycle_number=len(runtimes) + 1,
            started_at=cursor, start_reason="TEST_FIXTURE", charge_detection_confidence="HIGH",
        ))
        session.commit()


def test_baseline_health_trend_and_prediction_use_observed_runtime(app):
    seed_completed_cycles(app, [800, 820, 780, 760, 740, 720])
    service = detector_service(app)
    summary = service.summary("BAT-0001", START + timedelta(seconds=5400))
    assert summary["history"]["baseline_runtime_seconds"] == 800
    assert summary["health"]["runtime_health_ratio"] == pytest.approx(0.925)
    assert summary["health"]["trend"] in {"DECLINING_SLOWLY", "DECLINING_RAPIDLY"}
    assert summary["prediction"]["remaining_seconds"] is not None
    forecast = summary["replacement"]["forecast"]
    assert forecast["replace"]["days"] > 0
    assert forecast["replace"]["lower_days"] < forecast["replace"]["upper_days"]
    assert forecast["confidence"] in {"LOW", "MEDIUM", "HIGH"}
    assert summary["voltage"]["percentage"] is None


def test_battery_summary_and_cycle_reads_do_not_write_or_take_sqlite_writer_lock(app):
    seed_completed_cycles(app, [800, 820, 780, 760, 740, 720])
    statements = []
    engine = app.state.session_factory.kw["bind"]

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().upper())

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        service = detector_service(app)
        service.summary("BAT-0001")
        cycles = service.cycles("BAT-0001")
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
    assert cycles[1]["runtime_health_ratio"] == pytest.approx(0.9)
    assert not any(statement.startswith(("UPDATE", "INSERT", "DELETE")) for statement in statements)


def test_insufficient_and_excluded_cycles_remain_learning(app):
    seed_completed_cycles(app, [800, 100], eligible=False, observed_ratio=0.2)
    summary = detector_service(app).summary("BAT-0001", START + timedelta(seconds=1000))
    assert summary["health"]["status"] == "LEARNING"
    assert summary["prediction"]["confidence"] == "UNKNOWN"


@pytest.mark.parametrize(
    ("tail", "expected"),
    [([700, 690, 680], "PLAN_REPLACEMENT"), ([500, 490, 480], "REPLACE")],
)
def test_sustained_degradation_drives_replacement_state(app, tail, expected):
    seed_completed_cycles(app, [1000, 1000, 1000, *tail])
    summary = detector_service(app).summary("BAT-0001")
    assert summary["replacement"]["status"] == expected


def test_one_short_cycle_does_not_trigger_replacement(app):
    seed_completed_cycles(app, [1000, 1000, 1000, 400, 980, 990])
    assert detector_service(app).summary("BAT-0001")["replacement"]["status"] == "GOOD"


def test_stable_runtime_does_not_claim_a_replacement_date(app):
    seed_completed_cycles(app, [1000, 995, 1005, 1000, 998, 1002])
    forecast = detector_service(app).summary("BAT-0001")["replacement"]["forecast"]
    assert forecast["replace"] is None
    assert forecast["confidence"] == "UNKNOWN"
    assert "stable" in forecast["unavailable_reason"]


def test_battery_policy_validation_rejects_invalid_threshold_order():
    with pytest.raises(ValidationError, match="replace < plan replacement <= aging"):
        Settings(
            battery_replace_runtime_ratio=0.8,
            battery_plan_replacement_runtime_ratio=0.7,
            battery_aging_runtime_ratio=0.9,
        )

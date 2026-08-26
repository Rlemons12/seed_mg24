from pathlib import Path


ROOT = Path(__file__).parents[2]
FIRMWARE = ROOT / "sensor_package" / "firmware" / "xiao_mg24_sensor_node" / "xiao_mg24_sensor_node.ino"


def test_low_power_mode_has_bounded_wake_and_report_policy():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert "LOW_POWER_REPORT_INTERVAL_MS 300000UL" in source
    assert "LOW_POWER_SLEEP_SLICE_MS 1000UL" in source
    assert "LowPower.sleep(LOW_POWER_SLEEP_SLICE_MS)" in source
    assert 'command == "MODE LOW_POWER"' in source
    assert "publish_low_power_snapshot()" in source


def test_low_power_mode_gates_sensor_rails_and_vibration_work():
    source = FIRMWARE.read_text(encoding="utf-8")
    enter = source[source.index("void enter_low_power_mode() {") : source.index("void exit_low_power_mode() {")]
    snapshot = source[source.index("void publish_low_power_snapshot() {") : source.index("void print_imu_status() {")]
    assert "digitalWrite(IMU_POWER_PIN, LOW)" in enter
    assert "digitalWrite(BATTERY_ENABLE_PIN, LOW)" in enter
    assert "digitalWrite(IMU_POWER_PIN, HIGH)" in snapshot
    assert "digitalWrite(BATTERY_ENABLE_PIN, HIGH)" in snapshot
    assert "reporting_mode != LOW_POWER_MODE) vibration_service.service()" in source
    assert "if (reporting_mode == LOW_POWER_MODE) low_power_exit_pending = true" in source


def test_low_power_mode_is_advertised_and_runtime_only():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert '\\"low_power\\"' in source
    assert "reporting_mode = EDGE_SUMMARY_MODE" in source
    assert "low_power_exit_pending" in source

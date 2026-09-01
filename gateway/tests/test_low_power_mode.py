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
    assert "last_heartbeat_ms = last_low_power_report_ms" in enter
    assert "digitalWrite(IMU_POWER_PIN, HIGH)" in snapshot
    assert "digitalWrite(BATTERY_ENABLE_PIN, HIGH)" in snapshot
    assert "reporting_mode != LOW_POWER_MODE) vibration_service.service()" in source
    assert "LIVE_MODE_MAX_MS 600000UL" in source
    assert "live_mode_started_ms = millis();" in source
    assert "reporting_mode = LOW_POWER_MODE;\n    enter_low_power_mode();" in source


def test_low_power_mode_is_advertised_and_runtime_only():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert '\\"low_power\\"' in source
    assert "TelemetryMode reporting_mode = LIVE_MODE" in source
    assert 'command == "MODE EDGE_SUMMARY"' not in source
    assert "low_power_exit_pending" not in source


def test_mode_commands_restore_and_report_authoritative_firmware_state():
    source = FIRMWARE.read_text(encoding="utf-8")
    live = source[source.index('command == "MODE LIVE"') : source.index('command == "MODE LOW_POWER"')]
    low = source[source.index('command == "MODE LOW_POWER"') : source.index('command == "BLE START"')]
    exit_mode = source[source.index("void exit_low_power_mode() {") : source.index("void publish_low_power_snapshot() {")]
    assert "exit_low_power_mode();" in live
    assert "reporting_mode = LIVE_MODE;" in live
    assert 'command_result(true, "mode_live")' in live
    assert "reporting_mode = LOW_POWER_MODE;" in low
    assert "enter_low_power_mode();" in low
    assert 'command_result(true, "mode_low_power")' in low
    assert "digitalWrite(BATTERY_ENABLE_PIN, HIGH)" in exit_mode
    assert "digitalWrite(IMU_POWER_PIN, HIGH)" in exit_mode
    assert "imu_initialization_attempts = 0" in exit_mode
    assert "vibration_initialization_attempts = 0" in exit_mode
    assert "vibration_initialized = false" in exit_mode
    assert "initialize_imu();" in exit_mode


def test_current_telemetry_and_heartbeat_include_actual_runtime_mode():
    source = FIRMWARE.read_text(encoding="utf-8")
    telemetry = source[source.index("void ble_update_telemetry(", source.index("void ble_update_telemetry(") + 1) :]
    assert 'reporting_mode == LIVE_MODE ? "live" : "low_power"' in telemetry
    assert '\\"rm\\":\\"%s\\"' in telemetry
    assert "publish_low_power_snapshot()" in source
    assert "ble_update_telemetry(batt, ax, ay, az" in source
    assert "encode_heartbeat(" in source


def test_buffered_runtime_mode_evidence_is_marked_as_delayed():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert source.count("offline_buffer.mark_all_delayed();") >= 3
    assert 'snprintf(ble_json, sizeof(ble_json), "%.*s,\\\"d\\\":1}"' in source
    assert "record.delayed = !ble_connected;" in source
    assert "heartbeat.delayed = !ble_connected;" in source


def test_battery_adc_averaging_is_bounded():
    source = FIRMWARE.read_text(encoding="utf-8")
    battery = source[source.index("float battery_voltage() {") : source.index("void print_telemetry() {")]
    assert "kBatteryAdcSampleCount = 8" in battery
    assert battery.count("analogRead(BATTERY_ADC_PIN)") == 2
    assert "delay(1)" in battery
    assert "static_cast<float>(total) / static_cast<float>(kBatteryAdcSampleCount)" in battery

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIRMWARE = ROOT / "sensor_package/firmware/xiao_mg24_sensor_node/xiao_mg24_sensor_node.ino"
DASHBOARD = ROOT / "gateway/app/static/app.js"


def test_firmware_exposes_only_live_and_low_power_modes():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert "TelemetryMode reporting_mode = LOW_POWER_MODE" in source
    assert 'command == "MODE LIVE"' in source
    assert 'command == "MODE EDGE_SUMMARY"' not in source
    assert "capture_edge_sample()" in source
    assert "publish_edge_summary()" in source


def test_edge_summary_reports_averages_and_sample_count():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert "edge.battery / divisor" in source
    assert "edge.accel[0] / divisor" in source
    assert "edge.gyro[0] / divisor" in source
    assert "edge.analog[i] / divisor" in source
    assert "average_mic, mic_pct, analog_json, edge.count" in source
    assert '\\"sc\\":%lu' in source


def test_dashboard_exposes_only_live_and_low_power_mode_controls():
    source = DASHBOARD.read_text(encoding="utf-8")
    assert 'el("button", "Go Live")' in source
    assert 'command:"MODE LIVE"' in source
    assert 'el("button", "Use Edge Summary")' not in source
    assert 'command:"MODE EDGE_SUMMARY"' not in source
    assert 'command:"MODE LOW_POWER"' in source
    assert 'Telemetry mode: ${' in source
    assert 'batteryModeStatus.textContent = "Telemetry mode: LOW POWER"' in source
    assert "back in power-saving edge summary mode" not in source
    battery_panel = source.index('const battery = sensorPanel')
    mode_controls = source.index('const batteryModeActions')
    vibration_panel = source.index('const vibration = sensorPanel')
    assert battery_panel < mode_controls < vibration_panel

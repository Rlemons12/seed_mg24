from pathlib import Path


FIRMWARE = Path("sensor_package/firmware/xiao_mg24_sensor_node/xiao_mg24_sensor_node.ino")


def test_imu_initialization_is_bounded_retryable_and_diagnostic():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert "IMU_MAX_INITIALIZATION_ATTEMPTS 5" in source
    assert "IMU_RETRY_INTERVAL_MS 30000UL" in source
    assert "imu_initialization_attempts < IMU_MAX_INITIALIZATION_ATTEMPTS" in source
    assert "imu_who_am_i" in source
    assert 'command == "IMU STATUS"' in source
    assert "vibration_initialized = vibration_ok" in source


def test_live_reporting_does_not_depend_on_microphone_samples():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert "reporting_mode == LIVE_MODE &&\n      elapsed_since(now, last_sample_ms" in source
    assert "reporting_mode == LIVE_MODE && microphone_channel.report_due" not in source

from pathlib import Path

FIRMWARE = Path("sensor_package/firmware/xiao_mg24_sensor_node")


def test_bootstrap_mode_suppresses_telemetry_and_clears_runtime_before_reboot():
    sketch = (FIRMWARE / "xiao_mg24_sensor_node.ino").read_text(encoding="utf-8")
    assert "if (bootstrap_only) { delay(5); return; }" in sketch
    assert "offline_buffer.clear(); bootstrap_only = true" in sketch
    assert "systemReset();" in sketch
    assert 'command_result(false, "bootstrap_only")' in sketch


def test_factory_reset_is_usb_only_and_uses_secure_platform_random():
    usb = (FIRMWARE / "usb_bootstrap.cpp").read_text(encoding="utf-8")
    sketch = (FIRMWARE / "xiao_mg24_sensor_node.ino").read_text(encoding="utf-8")
    assert "sl_bt_system_get_random_data" in usb
    assert '"reset_protocol_version"' in usb
    assert "expected_hardware_id" in usb
    assert "prepare_factory_reset" not in sketch
    assert "confirm_factory_reset" not in sketch


def test_marker_is_registered_but_excluded_from_reset_deletion_array():
    keys = (FIRMWARE / "application_nvm_keys.h").read_text(encoding="utf-8")
    reset_array = keys.split("kApplicationFactoryReset[]", 1)[1].split("};", 1)[0]
    assert "kResetTransactionMarker = 0x0FF06" in keys
    assert "kResetTransactionMarker" not in reset_array

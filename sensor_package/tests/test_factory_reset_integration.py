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


def test_onboarding_identity_is_read_only_bootstrap_scoped_and_not_advertised():
    sketch = (FIRMWARE / "xiao_mg24_sensor_node.ino").read_text(encoding="utf-8")
    identity_block = sketch.split("onboarding_identity_characteristic_uuid", 1)[1]
    assert 'const char domain[] = "MG24-ONBOARDING-V1"' in sketch
    assert '#include "sha256_minimal.h"' in sketch
    assert "sha256_compute" in sketch and "getDeviceUniqueId()" in sketch
    assert "for (size_t index = 0; index < 16; ++index)" in sketch
    assert "SL_BT_GATTDB_CHARACTERISTIC_READ" in identity_block
    assert "SL_BT_GATTDB_CHARACTERISTIC_WRITE" not in identity_block.split("app_assert_status(sc);", 1)[0]
    assert '\\"provisioning_state\\\":\\\"provisioned\\\"' in sketch
    assert "ble_refresh_onboarding_identity();" in sketch.split("bootstrap_only = false;", 1)[1]
    advertising = sketch.split("void ble_start_advertising()", 2)[2]
    assert "onboarding_identity" not in advertising
    assert "prepare_factory_reset" not in sketch and "confirm_factory_reset" not in sketch

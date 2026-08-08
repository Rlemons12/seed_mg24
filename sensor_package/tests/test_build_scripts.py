import json
from pathlib import Path


def test_build_scripts_pin_ble_fqbn_and_keep_identity_provisioning_separate():
    package = Path(__file__).parents[1]
    for name in ("compile.ps1", "compile.sh"):
        text = (package / "scripts" / name).read_text(encoding="utf-8")
        assert "SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs" in text
        assert "SENSOR_PACKAGE_VERSION" in text and "PROTOCOL_VERSION" in text
    for name in ("flash.ps1", "flash.sh"):
        text = (package / "scripts" / name).read_text(encoding="utf-8")
        assert "arduino-cli upload" in text
        assert "masserase" not in text and "erase" not in text
    assert "provision_identity" in (package / "tools/bootstrap/cli.py").read_text(encoding="utf-8")


def test_only_one_active_production_sketch():
    root = Path(__file__).parents[2]
    sketches = [path for path in root.rglob("*.ino") if "diagnostics" not in path.parts]
    assert sketches == [root / "sensor_package/firmware/xiao_mg24_sensor_node/xiao_mg24_sensor_node.ino"]


def test_pinned_toolchain_matches_ble_build():
    package = Path(__file__).parents[1]
    toolchain = json.loads((package / "toolchain.json").read_text(encoding="utf-8"))
    assert toolchain["arduino_cli"]["tested_version"] == "1.5.1"
    assert toolchain["board_manager"]["core_version"] == "4.0.0"
    assert toolchain["board"]["options"]["protocol_stack"] == "ble_silabs"
    assert any(item["name"] == "Seeed Arduino LSM6DS3" and item["version"] == "2.0.7" for item in toolchain["libraries"])


def test_production_advertising_starts_only_from_ble_events():
    package = Path(__file__).parents[1]
    sketch = (
        package / "firmware/xiao_mg24_sensor_node/xiao_mg24_sensor_node.ino"
    ).read_text(encoding="utf-8")
    setup_body = sketch.split("void setup() {", 1)[1].split("#if BLE_SUPPORTED", 1)[0]

    assert "ble_start_advertising();" not in setup_body
    assert "bool ble_enabled = BLE_SUPPORTED;" in sketch
    assert "ble_enabled = BLE_SUPPORTED;" not in setup_body
    system_boot_case = sketch.split("case sl_bt_evt_system_boot_id:", 1)[1].split("break;", 1)[0]
    assert "ble_system_booted = true" in system_boot_case
    assert "ble_initialize_gatt_db();" not in system_boot_case
    assert "ble_start_advertising();" not in system_boot_case
    assert "application_setup_complete = true" in setup_body
    assert "ble_initialize_when_ready();" in sketch.split("void loop() {", 1)[1]


def test_large_gatt_values_are_initialized_in_bounded_chunks():
    package = Path(__file__).parents[1]
    sketch = (
        package / "firmware/xiao_mg24_sensor_node/xiao_mg24_sensor_node.ino"
    ).read_text(encoding="utf-8")

    assert "constexpr size_t kGattWriteChunkSize = 200;" in sketch
    assert "ble_write_attribute_chunks(ble_capabilities_characteristic_handle" in sketch
    assert "ble_write_attribute_chunks(ble_metadata_characteristic_handle" in sketch
    assert "strlen(capabilities_json), (const uint8_t*)capabilities_json" not in sketch
    assert "strlen(metadata_json), (const uint8_t*)metadata_json" not in sketch


def test_ble_command_callback_defers_persistence_to_main_loop():
    package = Path(__file__).parents[1]
    sketch = (package / "firmware/xiao_mg24_sensor_node/xiao_mg24_sensor_node.ino").read_text(encoding="utf-8")
    callback = sketch.split("case sl_bt_evt_gatt_server_attribute_value_id:", 1)[1].split("default:", 1)[0]
    loop = sketch.split("void loop() {", 1)[1]

    assert "ble_command_pending = true" in callback
    assert "handle_command" not in callback
    assert "runtime_configuration_store.write" not in callback
    assert "handle_command(String(command));" in loop


def test_assigned_configuration_transaction_cannot_change_identity():
    package = Path(__file__).parents[1]
    sketch = (package / "firmware/xiao_mg24_sensor_node/xiao_mg24_sensor_node.ino").read_text(encoding="utf-8")
    handler = sketch.split("bool handle_configuration_transaction", 1)[1].split("bool handle_config_command", 1)[0]

    assert 'command.startsWith("CFGSET ")' in handler
    assert "runtime_configuration_store.write" in handler
    assert "node_identity_store.provision" not in handler
    assert 'report_provisioning_state(transaction_id.c_str(), "configured")' in handler

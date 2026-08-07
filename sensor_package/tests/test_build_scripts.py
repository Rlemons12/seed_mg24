from pathlib import Path


def test_build_scripts_pin_ble_fqbn_and_flash_requires_identity():
    package = Path(__file__).parents[1]
    for name in ("compile.ps1", "compile.sh"):
        text = (package / "scripts" / name).read_text(encoding="utf-8")
        assert "SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs" in text
        assert "SENSOR_PACKAGE_VERSION" in text and "PROTOCOL_VERSION" in text
    for name in ("flash.ps1", "flash.sh"):
        text = (package / "scripts" / name).read_text(encoding="utf-8")
        assert "device_config.local.h" in text or "-Production" in text


def test_only_one_active_production_sketch():
    root = Path(__file__).parents[2]
    sketches = [path for path in root.rglob("*.ino") if "diagnostics" not in path.parts]
    assert sketches == [root / "sensor_package/firmware/xiao_mg24_sensor_node/xiao_mg24_sensor_node.ino"]

from pathlib import Path


FIRMWARE = Path("sensor_package/firmware/xiao_mg24_sensor_node/xiao_mg24_sensor_node.ino")


def test_production_firmware_excludes_microphone_hardware_activation():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert "#define ENABLE_MIC 0" in source
    assert "#if ENABLE_MIC\n#include <SilabsMicrophoneAnalog.h>" in source
    assert "#if ENABLE_MIC\nMicrophoneAnalog mic" in source
    assert "#if ENABLE_MIC\n  mic.begin" in source
    assert '\\"interface_id\\":\\"MIC\\"' not in source

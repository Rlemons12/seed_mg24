import shutil
import subprocess
from pathlib import Path

import pytest


def test_firmware_processing_modules(tmp_path):
    compiler = shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.skip("no host C++ compiler is installed")
    package = Path(__file__).parents[2]
    firmware = package / "firmware" / "xiao_mg24_sensor_node"
    executable = tmp_path / ("firmware-tests.exe" if shutil.which("g++") and "mingw" in compiler.lower() else "firmware-tests")
    sources = [
        "filters.cpp",
        "alarm_engine.cpp",
        "sensor_channel.cpp",
        "telemetry_buffer.cpp",
        "telemetry_encoder.cpp",
        "configuration_store.cpp",
    ]
    command = [
        compiler,
        "-std=c++11",
        "-Wall",
        "-Wextra",
        "-Werror",
        f"-I{firmware}",
        str(package / "tests" / "native" / "firmware_processing_tests.cpp"),
    ]
    command.extend(str(firmware / source) for source in sources)
    command.extend(["-o", str(executable)])
    subprocess.run(command, check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)

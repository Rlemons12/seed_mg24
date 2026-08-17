import shutil
import subprocess
from pathlib import Path

import pytest


def test_vibration_features(tmp_path):
    compiler = shutil.which("g++") or shutil.which("clang++")
    if compiler is None:
        pytest.skip("no host C++ compiler is installed")
    package = Path(__file__).parents[2]
    source = package / "firmware" / "libraries" / "SeedMg24Vibration" / "src"
    executable = tmp_path / "vibration-features-tests"
    command = [
        compiler,
        "-std=c++11",
        "-Wall",
        "-Wextra",
        "-Werror",
        f"-I{source}",
        str(package / "tests" / "native" / "vibration_features_tests.cpp"),
        str(source / "vibration_features.cpp"),
        str(source / "vibration_filter.cpp"),
        str(source / "vibration_fft.cpp"),
        str(source / "vibration_processor.cpp"),
        str(source / "vibration_double_buffer.cpp"),
        str(source / "vibration_fifo.cpp"),
        str(source / "vibration_runtime.cpp"),
        str(source / "vibration_summary.cpp"),
        "-o",
        str(executable),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)

#!/usr/bin/env python3
import compileall
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(label: str, command: list[str]) -> bool:
    print(f"\n[{label}] {' '.join(command)}", flush=True)
    return subprocess.run(command, cwd=ROOT, check=False).returncode == 0


def main() -> int:
    checks = [
        ("protocol", [sys.executable, "scripts/verify_compatibility.py"]),
        ("gateway", [sys.executable, "-m", "pytest", "gateway/tests", "-q"]),
        ("sensor-native", [sys.executable, "-m", "pytest", "sensor_package/tests", "-q"]),
    ]
    syntax_ok = compileall.compile_dir(ROOT / "gateway", quiet=1) and compileall.compile_dir(ROOT / "scripts", quiet=1)
    return 0 if syntax_ok and all(run(label, command) for label, command in checks) else 1


if __name__ == "__main__":
    sys.exit(main())

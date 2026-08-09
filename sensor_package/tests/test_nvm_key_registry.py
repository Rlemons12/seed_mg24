import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
FW = ROOT / "sensor_package/firmware/xiao_mg24_sensor_node"


def test_registered_keys_unique_and_in_verified_range():
    text = (FW / "application_nvm_keys.h").read_text()
    values = {name: int(value, 16) for name, value in re.findall(r"constexpr uint32_t (k\w+) = (0x[0-9A-Fa-f]+)u;", text)}
    keys = {k: v for k, v in values.items() if "Slot" in k or k in {"kConfigurationStaging", "kStoreMetadata"}}
    assert len(keys) == len(set(keys.values()))
    assert all(0x0FF00 <= value <= 0x0FF0F for value in keys.values())
    assert all(value > 0x0028 for value in keys.values())


def test_reset_scope_is_allowlisted_and_no_broad_erase():
    scope = json.loads((ROOT / "sensor_package/reset_scope.json").read_text())
    registered = {
        "identity_slot_a",
        "identity_slot_b",
        "configuration_slot_a",
        "configuration_slot_b",
        "configuration_staging",
        "store_metadata",
    }
    assert all(set(keys) <= registered for keys in scope["scopes"].values())
    sources = "\n".join(path.read_text(errors="ignore") for path in FW.glob("*.*") if path.suffix in {".h", ".cpp", ".ino"})
    forbidden = ["nvm3_eraseAll", "masserase", "flash erase_sector", "nvm3_erase"]
    assert not any(token in sources for token in forbidden)


def test_machine_readable_reset_scope_validator():
    result = subprocess.run(
        [sys.executable, str(ROOT / "sensor_package/scripts/verify_reset_scope.py")],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["status"] == "PASS"

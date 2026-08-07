#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    versions = {name: (ROOT / path / "VERSION").read_text(encoding="utf-8").strip() for name, path in
                {"sensor":"sensor_package", "gateway":"gateway", "protocol":"shared_protocol"}.items()}
    for name, value in versions.items():
        try:
            Version(value)
        except InvalidVersion as exc:
            raise ValueError(f"invalid {name} version: {value}") from exc
    matrix = json.loads((ROOT / "shared_protocol/compatibility.json").read_text(encoding="utf-8"))
    assert matrix["gateway_version"] == versions["gateway"]
    assert versions["protocol"] in matrix["supported_protocol_versions"]
    release = json.loads((ROOT / "sensor_package/RELEASE_INFO.json").read_text(encoding="utf-8"))
    assert release["version"] == versions["sensor"] and release["protocol_version"] == versions["protocol"]
    if not re.fullmatch(r"SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs", release["board_fqbn"]):
        raise ValueError("BLE-enabled board FQBN is missing")
    schemas = ROOT / "shared_protocol/schemas"
    for path in schemas.glob("*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema" or "type" not in schema:
            raise ValueError(f"invalid or unsupported JSON Schema declaration: {path.name}")
    metadata_schema = json.loads((schemas / "firmware_metadata.schema.json").read_text(encoding="utf-8"))
    metadata_fixture = json.loads((ROOT / "shared_protocol/fixtures/firmware_metadata.json").read_text(encoding="utf-8"))
    missing = set(metadata_schema["required"]) - set(metadata_fixture)
    if missing:
        raise ValueError(f"metadata fixture is missing required fields: {sorted(missing)}")
    print("compatibility and component versions valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())

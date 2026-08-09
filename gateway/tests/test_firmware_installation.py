import hashlib
import json

import pytest

from gateway.app.services.firmware_installation import ApprovedFirmwareCatalog, FirmwareValidationError, inspect_intel_hex


def hex_image(start: int, payload: bytes) -> str:
    upper = start >> 16
    offset = start & 0xFFFF

    def record(kind: int, address: int, data: bytes) -> str:
        body = bytes([len(data)]) + address.to_bytes(2, "big") + bytes([kind]) + data
        return ":" + (body + bytes([(-sum(body)) & 0xFF])).hex().upper()

    return "\n".join((record(4, 0, upper.to_bytes(2, "big")), record(0, offset, payload), record(1, 0, b""))) + "\n"


def catalog(tmp_path, *, start=0x08006000, end=None, filename="firmware.hex", sha=None, size=None, application_only=True):
    artifact = tmp_path / filename
    artifact.write_text(hex_image(start, b"approved"), encoding="ascii")
    end = end if end is not None else start + len(b"approved") - 1
    item = {
        "package_id": "approved-1", "firmware_version": "0.1.0", "protocol_version": "1.0.0",
        "fqbn": "SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs", "artifact_path": filename,
        "artifact_filename": filename, "artifact_size": size if size is not None else artifact.stat().st_size,
        "sha256": sha or hashlib.sha256(artifact.read_bytes()).hexdigest(), "application_start": f"0x{start:08X}",
        "application_end": f"0x{end:08X}", "bootloader_end": "0x08005FFF", "nvm3_start": "0x08174000",
        "build_provenance": "test fixture", "application_only": application_only,
    }
    manifest = tmp_path / "catalog.json"
    manifest.write_text(json.dumps({"schema_version": 1, "packages": [item]}), encoding="utf-8")
    return ApprovedFirmwareCatalog(tmp_path, manifest)


def test_approved_firmware_validates_hash_and_range(tmp_path):
    item, path = catalog(tmp_path).validate("approved-1")
    assert item["application_only"] and path.name == "firmware.hex"
    assert inspect_intel_hex(path) == (0x08006000, 0x08006007, 8)


@pytest.mark.parametrize("change", ["hash", "board", "with_bootloader", "bootloader_overlap", "nvm3_overlap", "unknown"])
def test_approved_firmware_rejects_unsafe_inputs(tmp_path, change):
    kwargs = {}
    if change == "hash": kwargs["sha"] = "00" * 32
    if change == "with_bootloader": kwargs["filename"] = "firmware.with_bootloader.hex"
    if change == "bootloader_overlap": kwargs["start"] = 0x08005000
    if change == "nvm3_overlap": kwargs["start"] = 0x08174000
    value = catalog(tmp_path, **kwargs)
    if change == "board":
        value._packages["approved-1"]["fqbn"] = "Other:board"
    with pytest.raises(FirmwareValidationError):
        value.validate("missing" if change == "unknown" else "approved-1")

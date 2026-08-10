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
    value = catalog(tmp_path)
    item, path = value.validate("approved-1")
    assert item["application_only"] and path.name == "firmware.hex"
    assert inspect_intel_hex(path) == (0x08006000, 0x08006007, 8)
    assert value.list()[0]["status"] == "ready"
    assert "artifact_path" not in value.list()[0]


def test_catalog_reports_missing_artifact_without_exposing_path(tmp_path):
    value = catalog(tmp_path)
    (tmp_path / "firmware.hex").unlink()

    package = value.list()[0]

    assert package["status"] == "missing"
    assert package["status_message"] == "approved firmware artifact is missing"
    assert "artifact_path" not in package
    with pytest.raises(FirmwareValidationError, match="artifact is missing") as error:
        value.validate("approved-1")
    assert error.value.code == "missing"


def test_catalog_reports_hash_mismatch(tmp_path):
    value = catalog(tmp_path, sha="00" * 32)

    package = value.list()[0]

    assert package["status"] == "hash_mismatch"
    assert package["status_message"] == "firmware artifact hash or size mismatch"


def test_developer_approval_is_disabled_by_default(tmp_path):
    value = catalog(tmp_path, sha="00" * 32)

    with pytest.raises(FirmwareValidationError, match="approval is disabled"):
        value.approve_development("approved-1")


def test_developer_approval_is_session_only_and_uses_actual_artifact(tmp_path):
    value = catalog(tmp_path, sha="00" * 32)
    value.developer_approval_enabled = True

    approved = value.approve_development("approved-1")

    assert approved["status"] == "developer_ready"
    assert approved["sha256"] == hashlib.sha256((tmp_path / "firmware.hex").read_bytes()).hexdigest().upper()
    assert approved["developer_approval_available"] is False
    assert value.validate("approved-1")[1].name == "firmware.hex"
    reloaded = ApprovedFirmwareCatalog(tmp_path, tmp_path / "catalog.json", developer_approval_enabled=True)
    assert reloaded.list()[0]["status"] == "hash_mismatch"


def test_developer_approval_rejects_protected_range(tmp_path):
    value = catalog(tmp_path, start=0x08005000, sha="00" * 32)
    value.developer_approval_enabled = True

    with pytest.raises(FirmwareValidationError, match="protected region"):
        value.approve_development("approved-1")


def test_install_api_returns_conflict_for_missing_artifact(client, tmp_path):
    value = catalog(tmp_path)
    (tmp_path / "firmware.hex").unlink()
    client.app.state.firmware_catalog = value
    client.app.state.firmware_installer.catalog = value

    packages = client.get("/api/firmware/packages")
    response = client.post(
        "/api/firmware/install",
        json={"hardware_serial": "E132D89F", "package_id": "approved-1"},
    )

    assert packages.status_code == 200
    assert packages.json()[0]["status"] == "missing"
    assert response.status_code == 409
    assert response.json() == {"detail": "approved firmware artifact is missing", "error": "request_error"}


def test_developer_approval_api_requires_confirmation_and_detected_board(client, tmp_path):
    value = catalog(tmp_path, sha="00" * 32)
    value.developer_approval_enabled = True
    client.app.state.firmware_catalog = value
    client.app.state.firmware_installer.catalog = value
    client.app.state.firmware_installer.boards = lambda: [{"hardware_serial": "E132D89F"}]
    body = {"hardware_serial": "E132D89F", "package_id": "approved-1"}

    missing_confirmation = client.post("/api/firmware/developer-approve", json={**body, "confirmation": "no"})
    wrong_board = client.post(
        "/api/firmware/developer-approve",
        json={**body, "hardware_serial": "AAAAAAAA", "confirmation": "APPROVE DEVELOPMENT FIRMWARE"},
    )
    approved = client.post(
        "/api/firmware/developer-approve",
        json={**body, "confirmation": "APPROVE DEVELOPMENT FIRMWARE"},
    )

    assert missing_confirmation.status_code == 422
    assert wrong_board.status_code == 409
    assert approved.status_code == 200
    assert approved.json()["status"] == "developer_ready"


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

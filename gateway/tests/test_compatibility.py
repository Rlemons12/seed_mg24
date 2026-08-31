from pathlib import Path
from types import SimpleNamespace

from gateway.app.main import _effective_device_metadata
from gateway.app.services.compatibility_service import CompatibilityService

MATRIX = Path(__file__).parents[2] / "shared_protocol" / "compatibility.json"


def metadata(version="0.1.0", protocol="1.0.0"):
    return {"sensor_package_version": version, "firmware_version": version, "protocol_version": protocol,
            "configuration_schema_version": 1, "build_identifier": "test"}


def test_compatibility_decisions():
    service = CompatibilityService(MATRIX)
    assert service.evaluate(metadata()).status == "compatible"
    assert service.evaluate({}).status == "metadata_missing"
    assert service.evaluate(metadata(protocol="2.0.0")).status == "protocol_unsupported"
    assert service.evaluate(metadata("0.0.1")).status == "sensor_update_required"
    assert service.evaluate(metadata("1.0.0")).status == "gateway_update_required"


def test_transient_empty_ble_metadata_preserves_last_verified_values():
    device = SimpleNamespace(
        sensor_package_version="0.1.2", firmware_version="0.1.2", protocol_version="1.1.0",
        configuration_schema_version=1, build_identifier="release", firmware_git_commit="abc",
        telemetry_schema_version=2,
    )
    assert _effective_device_metadata(device, {}) == {
        "sensor_package_version": "0.1.2", "firmware_version": "0.1.2", "protocol_version": "1.1.0",
        "configuration_schema_version": 1, "build_identifier": "release", "git_commit": "abc", "v": 2,
    }


def test_new_reported_metadata_replaces_last_verified_values():
    device = SimpleNamespace(
        sensor_package_version="0.1.1", firmware_version="0.1.1", protocol_version="1.1.0",
        configuration_schema_version=1, build_identifier="old", firmware_git_commit="old",
        telemetry_schema_version=1,
    )
    merged = _effective_device_metadata(device, {
        "sensor_package_version": "0.1.2", "firmware_version": "0.1.2", "build_identifier": "new",
    })
    assert merged["sensor_package_version"] == "0.1.2"
    assert merged["firmware_version"] == "0.1.2"
    assert merged["build_identifier"] == "new"
    assert merged["protocol_version"] == "1.1.0"

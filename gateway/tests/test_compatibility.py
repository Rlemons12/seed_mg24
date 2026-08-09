from pathlib import Path

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

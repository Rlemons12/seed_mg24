import json
from datetime import UTC, datetime
from pathlib import Path

from gateway.app.models import Reading
from gateway.app.repositories.device_repository import DeviceRepository

BUNDLED = Path(__file__).parents[2] / "sensor_package" / "profiles" / "built_in"


def create_node_and_discovery(client, app, compatible_discovery):
    response = client.post(
        "/api/devices", json={"device_id": "MG24-0001", "display_name": "Node", "discovery_address": compatible_discovery.address}
    )
    assert response.status_code == 201
    with app.state.session_factory() as session:
        repository = DeviceRepository(session)
        repository.update(repository.get("MG24-0001"), compatibility_status="compatible")


def installation_body():
    return {
        "node_id": "MG24-0001",
        "device_id": "ARM2001-01",
        "display_name": "Raw sensor",
        "sensor_profile_id": "generic.analog_raw",
        "sensor_profile_version": "1.0.0",
        "interface_id": "D0",
        "configuration": {
            "sample_interval_ms": 100,
            "processing_interval_ms": 100,
            "report_interval_ms": 100,
            "heartbeat_interval_ms": 30000,
            "filter_type": "none",
            "filter_window": 1,
            "change_deadband": None,
            "calibration_enabled": False,
            "calibration_gain": None,
            "calibration_offset": None,
            "alarms_enabled": False,
            "warning_low": None,
            "warning_high": None,
            "alarm_low": None,
            "alarm_high": None,
        },
    }


def test_profile_node_and_generic_onboarding_api(client, app, compatible_discovery):
    create_node_and_discovery(client, app, compatible_discovery)
    profiles = client.get("/api/sensor-profiles")
    assert profiles.status_code == 200
    generic = next(item for item in profiles.json() if item["profile_id"] == "generic.analog_raw")
    assert generic["status"] == "unverified" and generic["conversion"]["type"] == "unconfigured"
    interfaces = client.get("/api/nodes/MG24-0001/interfaces")
    assert interfaces.status_code == 200
    assert any(item["interface_id"] == "D0" and item["available"] for item in interfaces.json())
    draft = client.post("/api/sensor-installations", json=installation_body())
    assert draft.status_code == 201 and draft.json()["calibration_status"] == "not_configured"
    installation_id = draft.json()["installation_id"]
    assert client.post(f"/api/sensor-installations/{installation_id}/validate").json()["provisioning_state"] == "ready_to_apply"


def test_apply_requires_recent_interface_telemetry(client, app, compatible_discovery):
    create_node_and_discovery(client, app, compatible_discovery)
    draft = client.post("/api/sensor-installations", json=installation_body()).json()
    installation_id = draft["installation_id"]
    client.post(f"/api/sensor-installations/{installation_id}/validate")
    failed = client.post(f"/api/sensor-installations/{installation_id}/apply")
    assert failed.status_code == 409 and "telemetry" in failed.json()["detail"]


def test_profile_validation_and_upload_limits(client, app):
    value = json.loads((BUNDLED / "generic-analog-raw-1.0.0.json").read_text())
    value["profile_version"] = "1.0.1"
    assert client.post("/api/sensor-profiles/validate", json={"profile": value}).status_code == 200
    imported = client.post("/api/sensor-profiles/import", content=json.dumps(value), headers={"content-type": "application/json"})
    assert imported.status_code == 201
    oversized = client.post(
        "/api/sensor-profiles/import",
        content=b"x" * (app.state.settings.max_profile_upload_bytes + 1),
        headers={"content-type": "application/json"},
    )
    assert oversized.status_code == 413


def test_explicit_profile_upgrade_preserves_history(client, app, compatible_discovery):
    create_node_and_discovery(client, app, compatible_discovery)
    draft = client.post("/api/sensor-installations", json=installation_body()).json()
    value = json.loads((BUNDLED / "generic-analog-raw-1.0.0.json").read_text())
    value["profile_version"] = "1.1.0"
    assert (
        client.post("/api/sensor-profiles/import", content=json.dumps(value), headers={"content-type": "application/json"}).status_code
        == 201
    )
    with app.state.session_factory() as session:
        node = DeviceRepository(session).get("MG24-0001")
        session.add(
            Reading(
                registered_device_id=node.id,
                received_at=datetime.now(UTC),
                device_uptime_ms=1,
                measured_at_device_uptime=1,
                sequence_number=9,
                session_id="boot",
                record_type="measurement",
                channel="analog_0",
                raw_value=10,
                normalized_value=10,
                unit="adc_count",
                quality="uncalibrated",
                payload_json="{}",
                delayed=False,
            )
        )
        session.commit()
    upgraded = client.post(f"/api/sensor-installations/{draft['installation_id']}/upgrade-profile", json={"profile_version": "1.1.0"})
    assert upgraded.status_code == 200 and upgraded.json()["sensor_profile_version"] == "1.1.0"
    with app.state.session_factory() as session:
        assert session.query(Reading).count() == 1

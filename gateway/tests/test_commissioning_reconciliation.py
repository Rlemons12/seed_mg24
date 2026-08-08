from pathlib import Path

CONFIGURATION = {
    "sample_interval_ms": 100,
    "processing_interval_ms": 100,
    "report_interval_ms": 100,
    "heartbeat_interval_ms": 30000,
    "filter_type": "ema",
    "filter_window": 2,
    "calibration_enabled": False,
    "alarms_enabled": False,
}


class AssignedProvisioner:
    def __init__(self, node_id="MG24-0002"):
        self.node_id = node_id
        self.provision_calls = 0

    async def read_state(self, _address):
        return {
            "metadata": {"node_id": "UNASSIGNED-MG24", "protocol_version": "1.0.0"},
            "capabilities": {"configuration": {"readback": True}},
            "readback": {"t": "ca", "code": "readback", "id": self.node_id},
        }

    async def provision(self, *_args, **_kwargs):
        self.provision_calls += 1
        raise AssertionError("assigned devices must not enter provisioning")


def request_body(node_id="MG24-0001"):
    return {
        "discovery_address": "AA:BB:CC:DD:EE:01",
        "node_id": node_id,
        "display_name": "XIAO MG24 Sense 01",
        "idempotency_key": "409c0ffee1de0001",
        "configuration": CONFIGURATION,
    }


def test_assigned_device_is_reconciled_and_not_offered_for_commissioning(client, app, compatible_discovery):
    provisioner = AssignedProvisioner()
    app.state.node_provisioner = provisioner

    discoveries = client.get("/api/commissioning/discoveries").json()
    assert discoveries[0]["commissioning_state"] == "assigned_elsewhere"
    assert discoveries[0]["reported_node_id"] == "MG24-0002"
    assert discoveries[0]["temporary_id"] is None
    assert discoveries[0]["action"] == "recovery_or_import"

    response = client.post("/api/commissioning/nodes", json=request_body())
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "device_already_assigned",
        "message": "This MG24 is already assigned as MG24-0002; application firmware installation preserves identity.",
        "assigned_node_id": "MG24-0002",
        "recovery": "Use its original dashboard database or the documented USB application-factory recovery workflow.",
    }
    assert provisioner.provision_calls == 0
    assert client.get("/api/devices").json() == []
    assert client.get("/api/sensor-installations").json() == []


def test_existing_local_device_routes_to_reconnect_without_duplicates(client, app, compatible_discovery):
    provisioner = AssignedProvisioner()
    app.state.node_provisioner = provisioner
    created = client.post(
        "/api/devices",
        json={"device_id": "MG24-0002", "display_name": "Existing node",
              "discovery_address": compatible_discovery.address},
    )
    assert created.status_code == 201

    discovery = client.get("/api/commissioning/discoveries").json()[0]
    assert discovery["commissioning_state"] == "registered_here"
    assert discovery["action"] == "view_or_reconnect"
    assert discovery["local_device_id"] == "MG24-0002"
    assert discovery["temporary_id"] is None

    response = client.post("/api/commissioning/nodes", json=request_body())
    assert response.status_code == 200
    assert response.json()["device_id"] == "MG24-0002"
    assert len(client.get("/api/devices").json()) == 1
    assert provisioner.provision_calls == 0


def test_dashboard_does_not_retry_conflict_and_explains_firmware_persistence():
    script = Path("gateway/app/static/app.js").read_text(encoding="utf-8")
    template = Path("gateway/app/templates/index.html").read_text(encoding="utf-8")
    submit = script.split('$("node-form").addEventListener("submit"', 1)[1].split(
        '$("usb-detect-button")', 1
    )[0]
    assert submit.count('api("/api/commissioning/nodes"') == 1
    assert "while" not in submit
    assert "/api/commissioning/discoveries" in script
    assert script.count("Reconnect / View Sensor") >= 2
    assert "Existing identity and configuration were preserved" in script
    assert "Reinstalling application firmware preserves" in template

import json
import subprocess
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


class UnassignedProvisioner(AssignedProvisioner):
    def __init__(self):
        super().__init__("UNASSIGNED-MG24")

    async def provision(self, _address, node_id, _transaction_id, _configuration):
        self.provision_calls += 1
        return {"metadata": {"firmware_version": "0.1.0", "sensor_package_version": "0.1.0",
                             "protocol_version": "1.0.0", "configuration_schema_version": 1},
                "readback": {"id": node_id}}


class ConfiguringProvisioner(AssignedProvisioner):
    FILTERS = {"none": 0, "moving_average": 1, "ema": 2, "median": 3}

    def __init__(self):
        super().__init__("MG24-0002")
        self.configure_calls = 0

    async def read_state(self, _address):
        return {
            "metadata": {"node_id": self.node_id, "firmware_version": "0.1.0", "protocol_version": "1.0.0"},
            "capabilities": {"node_id": self.node_id},
            "readback": {"id": self.node_id, "sample": 100, "process": 100, "report": 100,
                         "heartbeat": 30000, "filter": 2, "window": 2, "enabled": 1},
        }

    async def configure(self, _address, node_id, transaction_id, configuration):
        self.configure_calls += 1
        assert node_id == self.node_id
        return {"acknowledgement": {"code": "configured", "tx": transaction_id.upper()},
                "readback": {"id": node_id, "report": configuration["report_interval_ms"]}}


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
    assert discoveries[0]["assigned_node_id"] == "MG24-0002"
    assert discoveries[0]["temporary_id"] is None
    assert discoveries[0]["action"] == "import"
    assert discoveries[0]["commissioning_eligible"] is False

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
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "device_already_registered"
    assert len(client.get("/api/devices").json()) == 1
    assert provisioner.provision_calls == 0


def test_import_assigned_device_creates_node_only_and_performs_no_write(client, app, compatible_discovery):
    provisioner = AssignedProvisioner()
    app.state.node_provisioner = provisioner
    response = client.post("/api/commissioning/import", json={
        "discovery_address": compatible_discovery.address, "display_name": "Imported MG24",
    })
    assert response.status_code == 200
    assert response.json()["device_id"] == "MG24-0002"
    assert provisioner.provision_calls == 0
    assert len(client.get("/api/devices").json()) == 1
    assert client.get("/api/sensor-installations").json() == []


def test_device_configuration_is_separate_from_installations_and_idempotent(client, app, compatible_discovery):
    provisioner = ConfiguringProvisioner()
    app.state.node_provisioner = provisioner
    created = client.post("/api/devices", json={
        "device_id": "MG24-0002", "display_name": "Existing node",
        "discovery_address": compatible_discovery.address,
    })
    assert created.status_code == 201
    body = {**CONFIGURATION, "transaction_id": "409c0ffee1de0001", "report_interval_ms": 200}
    first = client.post("/api/nodes/MG24-0002/configuration", json=body)
    second = client.post("/api/nodes/MG24-0002/configuration", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert provisioner.configure_calls == 1
    assert client.get("/api/sensor-installations").json() == []


def test_dashboard_does_not_retry_conflict_and_explains_firmware_persistence():
    script = Path("gateway/app/static/app.js").read_text(encoding="utf-8")
    template = Path("gateway/app/templates/index.html").read_text(encoding="utf-8")
    submit = script.split('$("node-form").addEventListener("submit"', 1)[1].split(
        '$("usb-detect-button")', 1
    )[0]
    assert submit.count('api("/api/commissioning/nodes"') == 1
    assert "while" not in submit
    assert "/api/commissioning/discoveries" in script
    assert "Open Sensor" in script
    assert "Existing identity and configuration were preserved" in script
    assert "Application installation preserves" in template
    assert 'id="new-node-fields" class="hidden"' in template
    assert 'id="provision-node-button" class="primary" disabled' in template
    assert "Explicitly allow an unconfirmed device" not in template


def test_actual_client_state_reducer_is_fail_closed_and_clears_stale_values():
    module = Path("gateway/app/static/onboarding_state.js").resolve()
    script = f"""
const m = require({json.dumps(str(module))});
console.log(JSON.stringify([
  m.transition(),
  m.transition({{
    commissioning_state: 'assigned_elsewhere',
    action: 'recovery_or_import',
    reported_node_id: 'MG24-0002'
  }}, 'classified'),
  m.transition({{commissioning_state: 'unassigned', commissioning_eligible: true,
    temporary_id: 'unassigned:aa:bb:cc:dd:ee:01', compatible: true, action: 'commission'}}, 'classified')
]));
"""
    pending, assigned, unassigned = json.loads(subprocess.check_output(["node", "-e", script], text=True))
    assert pending["canProvision"] is False and pending["selectedDiscovery"] is None
    assert assigned["status"] == "Already assigned as MG24-0002."
    assert assigned["canProvision"] is False and assigned["showRecovery"] is True
    assert assigned["nodeId"] == assigned["displayName"] == assigned["location"] == ""
    assert unassigned["canProvision"] is True and unassigned["showProvisioningFields"] is True


def test_genuinely_unassigned_device_still_commissions_after_authoritative_readback(client, app, compatible_discovery):
    provisioner = UnassignedProvisioner()
    app.state.node_provisioner = provisioner
    discovery = client.get("/api/commissioning/discoveries").json()[0]
    assert discovery["commissioning_state"] == "unassigned"
    assert discovery["commissioning_eligible"] is True
    response = client.post("/api/commissioning/nodes", json=request_body("MG24-0003"))
    assert response.status_code == 200
    assert response.json()["device_id"] == "MG24-0003"
    assert provisioner.provision_calls == 1
    assert len(client.get("/api/devices").json()) == 1


def test_dashboard_assets_are_revalidated_on_refresh(client):
    assert client.get("/").headers["cache-control"] == "no-cache, must-revalidate"
    assert client.get("/static/app.js?v=test").headers["cache-control"] == "no-cache, must-revalidate"
    html = client.get("/").text
    assert "live-inputs-1" in html
    script = Path("gateway/app/static/app.js").read_text(encoding="utf-8")
    assert 'socket.addEventListener("message",scheduleRefresh)' in script

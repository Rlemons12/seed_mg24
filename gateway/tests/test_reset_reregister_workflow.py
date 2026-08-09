import json
from pathlib import Path
from types import MethodType

from sqlalchemy import func, select

from gateway.app.ble.onboarding_identity import derive_onboarding_identity
from gateway.app.models import Reading, SensorReregistrationWorkflow
from gateway.app.repositories.device_repository import DeviceRepository


def register_with_identity(client, app, discovery, hardware_id="0x0123456789ABCDEF"):
    response = client.post("/api/devices", json={
        "device_id": "MG24-0001", "display_name": "Boiler sensor", "discovery_address": discovery.address,
    })
    assert response.status_code == 201
    with app.state.session_factory() as session:
        device = DeviceRepository(session).get("MG24-0001")
        device.hardware_id = hardware_id
        device.firmware_version = "1.2.3"
        session.commit()


def test_start_requires_immutable_identity_and_persists_resumable_safe_state(client, app, compatible_discovery):
    response = client.post("/api/devices", json={
        "device_id": "MG24-0001", "display_name": "Boiler sensor", "discovery_address": compatible_discovery.address,
    })
    assert response.status_code == 201
    blocked = client.post("/api/reset-reregister/start", json={"device_id": "MG24-0001"})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "hardware_id_required"
    with app.state.session_factory() as session:
        DeviceRepository(session).get("MG24-0001").hardware_id = "0x0123456789ABCDEF"
        session.commit()
    first = client.post("/api/reset-reregister/start", json={"device_id": "MG24-0001"})
    second = client.post("/api/reset-reregister/start", json={"device_id": "MG24-0001"})
    assert first.status_code == 200 and first.json()["operation_id"] == second.json()["operation_id"]
    assert first.json()["state"] == "usb_connection_required"
    serialized = json.dumps(first.json()).lower()
    assert "confirmation_token" not in serialized and "challenge" not in serialized and "secret" not in serialized
    assert client.get("/api/reset-reregister/incomplete").json()[0]["operation_id"] == first.json()["operation_id"]


def test_usb_selection_requires_exact_hardware_and_backup_is_allowlisted(client, app, compatible_discovery):
    register_with_identity(client, app, compatible_discovery)

    class FakeUsb:
        def ports(self):
            return [{"port": "COM7", "usb_serial": "SERIAL", "usb_vid_pid": "2886:0062"}]

        def inspect(self, port):
            assert port == "COM7"
            return {"port": port, "hardware_id": "0x0123456789ABCDEF", "node_id": "MG24-0001",
                    "firmware_version": "1.2.3", "provisioning_state": "provisioned",
                    "configuration": {"sample": 100, "heartbeat": 30000, "wifi_password": "must-not-leak"}}

    app.state.usb_factory_reset = FakeUsb()
    operation = client.post("/api/reset-reregister/start", json={"device_id": "MG24-0001"}).json()
    wrong = client.post(f"/api/reset-reregister/{operation['operation_id']}/select-usb", json={
        "port": "COM7", "expected_hardware_id": "0xFEDCBA9876543210",
    })
    assert wrong.status_code == 409
    selected = client.post(f"/api/reset-reregister/{operation['operation_id']}/select-usb", json={
        "port": "COM7", "expected_hardware_id": "0x0123456789ABCDEF",
    })
    assert selected.status_code == 200 and selected.json()["state"] == "physical_identity_verified"
    backed_up = client.post(f"/api/reset-reregister/{operation['operation_id']}/backup", json={})
    assert backed_up.status_code == 200 and backed_up.json()["backup_status"] == "complete"
    with app.state.session_factory() as session:
        stored = session.get(SensorReregistrationWorkflow, operation["operation_id"])
        assert "wifi_password" not in stored.backup_json and json.loads(stored.backup_json)["configuration"]["sample"] == 100


def test_registration_choice_rejects_duplicate_and_never_moves_history(client, app, compatible_discovery):
    register_with_identity(client, app, compatible_discovery)
    with app.state.session_factory() as session:
        source = DeviceRepository(session).get("MG24-0001")
        session.add(Reading(registered_device_id=source.id, session_id="old", channel="temperature",
                            raw_value=1, payload_json="{}"))
        session.commit()
    operation = client.post("/api/reset-reregister/start", json={"device_id": "MG24-0001"}).json()
    with app.state.session_factory() as session:
        row = session.get(SensorReregistrationWorkflow, operation["operation_id"])
        row.state = "unprovisioned_ready_for_registration"
        session.commit()
    payload = {"choice": "restore", "device_id": "MG24-OTHER", "display_name": "Boiler sensor",
               "location": None, "configuration": {}}
    mismatch = client.post(f"/api/reset-reregister/{operation['operation_id']}/registration", json=payload)
    assert mismatch.status_code == 409 and mismatch.json()["detail"]["code"] == "restore_identity_mismatch"
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Reading)) == 1


def test_workflow_endpoints_enforce_same_origin_and_usb_loopback(client, app, compatible_discovery):
    register_with_identity(client, app, compatible_discovery)
    assert client.post("/api/reset-reregister/start", json={"device_id": "MG24-0001"},
                       headers={"Origin": "https://evil.example"}).status_code == 403
    operation = client.post("/api/reset-reregister/start", json={"device_id": "MG24-0001"}).json()
    response = client.post(f"/api/reset-reregister/{operation['operation_id']}/detect-usb", json={},
                           headers={"X-Forwarded-For": "127.0.0.1"})
    assert response.status_code == 403
    oversized = client.request("POST", "/api/reset-reregister/start", content=b'{' + b'"x":"' + b'a' * 5000 + b'"}',
                               headers={"Content-Type": "application/json", "Origin": "http://testserver"})
    assert oversized.status_code == 413
    assert client.get(f"/api/reset-reregister/{operation['operation_id']}").status_code == 200


def test_dashboard_wizard_is_focused_accessible_and_keeps_separate_actions():
    root = Path(__file__).parents[2]
    script = (root / "gateway/app/static/reset_reregister.js").read_text(encoding="utf-8")
    app_script = (root / "gateway/app/static/app.js").read_text(encoding="utf-8")
    template = (root / "gateway/app/templates/index.html").read_text(encoding="utf-8")
    assert "Reset and Re-register Sensor" in script and "workflow-stepper" in script
    assert 'role="status" aria-live="polite"' in script and 'aria-label="Close reset and re-register workflow"' in script
    assert "alert(" not in script and "confirm(" not in script and "prompt(" not in script
    assert "confirmation_token" not in script and "challenge" not in script
    assert "Remove from network" in app_script and "Restore/Reapprove" in app_script
    assert "Factory Reset Sensor" in app_script and "Reset and Re-register" in app_script
    assert "reset_reregister.js" in template


def prepare_ble_workflow(client, app, discovery):
    register_with_identity(client, app, discovery)
    operation = client.post("/api/reset-reregister/start", json={"device_id": "MG24-0001"}).json()
    with app.state.session_factory() as session:
        row = session.get(SensorReregistrationWorkflow, operation["operation_id"])
        row.state = "searching_for_reset_sensor_ble"
        session.commit()

    async def start_scan(_self): return True
    async def wait_for_scan(_self): return None
    app.state.scanner.start_scan = MethodType(start_scan, app.state.scanner)
    app.state.scanner.wait_for_scan = MethodType(wait_for_scan, app.state.scanner)
    return operation


def test_ble_scan_selects_only_exact_usb_identity_and_ignores_rssi(client, app, compatible_discovery):
    operation = prepare_ble_workflow(client, app, compatible_discovery)
    wrong = compatible_discovery.model_copy(update={"address": "AA:BB:CC:DD:EE:99", "rssi": -5})
    app.state.scanner.record(wrong)
    expected = derive_onboarding_identity("0x0123456789ABCDEF")

    class Provisioner:
        async def read_onboarding_identity(self, address):
            identity = expected if address == compatible_discovery.address else "0" * 32
            return {"schema_version": 1, "onboarding_identity": identity, "provisioning_state": "unprovisioned",
                    "protocol_version": "1.0.0", "firmware_version": "0.1.0"}

    app.state.node_provisioner = Provisioner()
    response = client.post(f"/api/reset-reregister/{operation['operation_id']}/scan-ble", json={})
    assert response.status_code == 200 and response.json()["state"] == "ble_identity_matched"
    candidates = {row["address"]: row for row in response.json()["candidates"]}
    assert candidates[compatible_discovery.address]["verification_status"] == "verified_match"
    assert candidates[wrong.address]["verification_status"] == "non_match"
    assert not candidates[wrong.address]["provisioning_allowed"]


def test_duplicate_claimed_identity_blocks_and_manual_selection_cannot_bypass(client, app, compatible_discovery):
    operation = prepare_ble_workflow(client, app, compatible_discovery)
    duplicate = compatible_discovery.model_copy(update={"address": "AA:BB:CC:DD:EE:88", "rssi": -20})
    app.state.scanner.record(duplicate)
    expected = derive_onboarding_identity("0x0123456789ABCDEF")

    class DuplicateProvisioner:
        async def read_onboarding_identity(self, _address):
            return {"schema_version": 1, "onboarding_identity": expected, "provisioning_state": "unprovisioned",
                    "protocol_version": "1.0.0", "firmware_version": "0.1.0"}

    app.state.node_provisioner = DuplicateProvisioner()
    scanned = client.post(f"/api/reset-reregister/{operation['operation_id']}/scan-ble", json={})
    assert scanned.status_code == 200 and scanned.json()["state"] == "searching_for_reset_sensor_ble"
    assert {row["verification_status"] for row in scanned.json()["candidates"]} == {"duplicate_identity"}

    class MismatchProvisioner:
        async def read_onboarding_identity(self, _address):
            return {"schema_version": 1, "onboarding_identity": "f" * 32, "provisioning_state": "unprovisioned",
                    "protocol_version": "1.0.0", "firmware_version": "0.1.0"}

    app.state.node_provisioner = MismatchProvisioner()
    selected = client.post(f"/api/reset-reregister/{operation['operation_id']}/select-ble",
                           json={"address": compatible_discovery.address})
    assert selected.status_code == 409 and selected.json()["detail"]["code"] == "ble_identity_mismatch"

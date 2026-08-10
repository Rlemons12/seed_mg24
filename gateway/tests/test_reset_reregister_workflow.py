import json
from pathlib import Path
from types import MethodType

import pytest
from sqlalchemy import func, select

from gateway.app.ble.onboarding_identity import derive_onboarding_identity
from gateway.app.models import DeviceLifecycleEvent, Reading, SensorReregistrationWorkflow
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.services.ble_provisioning import BleProvisioningError


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


def test_usb_association_requires_exact_current_node_identity(client, app, compatible_discovery):
    response = client.post("/api/devices", json={
        "device_id": "MG24-0001", "display_name": "Boiler sensor", "discovery_address": compatible_discovery.address,
    })
    assert response.status_code == 201

    class FakeUsb:
        def inspect(self, port):
            assert port == "COM7"
            return {"port": port, "hardware_id": "0x0123456789ABCDEF", "node_id": "MG24-0001", "identity_status": "ok"}

    app.state.usb_factory_reset = FakeUsb()
    mismatch = client.post("/api/reset-reregister/associate-usb", json={
        "device_id": "MG24-0001", "port": "COM7", "expected_hardware_id": "0xFEDCBA9876543210",
    })
    associated = client.post("/api/reset-reregister/associate-usb", json={
        "device_id": "MG24-0001", "port": "COM7", "expected_hardware_id": "0x0123456789ABCDEF",
    })

    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "physical_identity_mismatch"
    assert associated.status_code == 200
    assert associated.json()["status"] == "associated"
    assert client.post("/api/reset-reregister/start", json={"device_id": "MG24-0001"}).status_code == 200


def test_reconcile_returns_to_safe_retry_when_reset_was_not_applied(client, app, compatible_discovery):
    register_with_identity(client, app, compatible_discovery)
    operation = client.post("/api/reset-reregister/start", json={"device_id": "MG24-0001"}).json()
    with app.state.session_factory() as session:
        workflow = session.get(SensorReregistrationWorkflow, operation["operation_id"])
        workflow.state = "recoverable_error"
        workflow.backup_status = "complete"
        workflow.selected_port = "COM7"
        workflow.reset_operation_id = "reset-attempt"
        device = DeviceRepository(session).get("MG24-0001")
        device.factory_reset_status = "reset_pending"
        session.commit()

    class IntactUsb:
        def ports(self):
            return [{"port": "COM7"}]

        def inspect(self, port):
            assert port == "COM7"
            return {
                "hardware_id": "0x0123456789ABCDEF", "node_id": "MG24-0001",
                "identity_status": "ok", "provisioning_state": "provisioned", "configuration_status": "ok",
            }

    app.state.usb_factory_reset = IntactUsb()
    response = client.post(
        f"/api/reset-reregister/{operation['operation_id']}/reconcile-reset",
        json={"expected_hardware_id": "0x0123456789ABCDEF"},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "configuration_backup_ready"
    assert response.json()["result"]["reconciliation"]["physical_reset_applied"] is False
    with app.state.session_factory() as session:
        assert DeviceRepository(session).get("MG24-0001").factory_reset_status == "not_requested"


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


def test_registration_details_can_be_reopened_only_before_provisioning(client, app, compatible_discovery):
    register_with_identity(client, app, compatible_discovery)
    operation = client.post("/api/reset-reregister/start", json={"device_id": "MG24-0001"}).json()
    with app.state.session_factory() as session:
        row = session.get(SensorReregistrationWorkflow, operation["operation_id"])
        row.state = "searching_for_reset_sensor_ble"
        row.registration_choice = "restore"
        row.target_device_id = "MG24-0001"
        row.target_display_name = "Boiler sensor"
        row.configuration_json = "{}"
        session.commit()

    reopened = client.post(f"/api/reset-reregister/{operation['operation_id']}/edit-registration", json={})

    assert reopened.status_code == 200
    assert reopened.json()["state"] == "registration_details_required"
    assert reopened.json()["registration_choice"] is None
    assert reopened.json()["target_device_id"] is None
    with app.state.session_factory() as session:
        row = session.get(SensorReregistrationWorkflow, operation["operation_id"])
        row.state = "provisioning_in_progress"
        session.commit()
    blocked = client.post(f"/api/reset-reregister/{operation['operation_id']}/edit-registration", json={})
    assert blocked.status_code == 409


def test_revised_registration_choice_creates_a_distinct_audit_event(client, app, compatible_discovery):
    register_with_identity(client, app, compatible_discovery)
    operation = client.post("/api/reset-reregister/start", json={"device_id": "MG24-0001"}).json()
    with app.state.session_factory() as session:
        row = session.get(SensorReregistrationWorkflow, operation["operation_id"])
        row.state = "unprovisioned_ready_for_registration"
        session.commit()
    restore = {
        "choice": "restore", "device_id": "MG24-0001", "display_name": "Boiler sensor",
        "location": None, "configuration": {},
    }
    assert client.post(f"/api/reset-reregister/{operation['operation_id']}/registration", json=restore).status_code == 200
    assert client.post(f"/api/reset-reregister/{operation['operation_id']}/edit-registration", json={}).status_code == 200

    revised = client.post(f"/api/reset-reregister/{operation['operation_id']}/registration", json={
        **restore, "choice": "new", "device_id": "AU-VS-M-0001", "display_name": "au-vs-m-0001",
    })

    assert revised.status_code == 200
    assert revised.json()["target_device_id"] == "AU-VS-M-0001"


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
    assert 'if(sensorId.value!==operation.source_device_id)choice.value="new"' in script
    assert 'if(actionError)id("rr-error").textContent=actionError' in script
    assert 'choice.value=editingNewIdentity?"new":"restore"' in script
    assert 'operation.state === "gateway_registration_in_progress"' in script
    assert "Continue Gateway Registration" in script
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


def test_provision_registers_after_discovery_cache_expires_and_retry_is_read_only(client, app, compatible_discovery):
    operation = prepare_ble_workflow(client, app, compatible_discovery)
    with app.state.session_factory() as session:
        row = session.get(SensorReregistrationWorkflow, operation["operation_id"])
        row.state = "ble_identity_matched"
        row.registration_choice = "new"
        row.target_device_id = "AU-VS-M-0001"
        row.target_display_name = "Vibration sensor"
        row.target_ble_address = compatible_discovery.address
        row.configuration_json = json.dumps({
            "sample_interval_ms": 100, "processing_interval_ms": 1000, "report_interval_ms": 5000,
            "heartbeat_interval_ms": 30000, "filter_type": "none", "filter_window": 1, "enabled": True,
        })
        result = json.loads(row.result_json)
        result["sensor_provisioned"] = True
        row.result_json = json.dumps(result)
        source = DeviceRepository(session).get("MG24-0001")
        source.archived = True
        source.lifecycle_state = "removed"
        session.add(DeviceLifecycleEvent(
            operation_id=f"{operation['operation_id']}-provision", event_type="ble_provisioned",
            device_id="AU-VS-M-0001", display_name="Vibration sensor", hardware_id="0x0123456789ABCDEF",
            ble_address=compatible_discovery.address, connectivity_state="connecting",
            method="reset_reregister_workflow", factory_reset_requested=True, result="success", detail_json="{}",
        ))
        session.commit()
    app.state.scanner._discoveries.clear()

    class ReadOnlyResumeProvisioner:
        async def provision(self, *_args, **_kwargs):
            raise AssertionError("retry must not repeat provisioning")

        async def read_state(self, address):
            assert address == compatible_discovery.address
            return {"metadata": {"node_id": "AU-VS-M-0001"}, "capabilities": {},
                    "readback": {"id": "AU-VS-M-0001"}}

    app.state.node_provisioner = ReadOnlyResumeProvisioner()
    response = client.post(f"/api/reset-reregister/{operation['operation_id']}/provision", json={})

    assert response.status_code == 202
    assert response.json()["state"] == "network_verification_in_progress"
    with app.state.session_factory() as session:
        device = DeviceRepository(session).get("AU-VS-M-0001")
        assert device is not None and device.ble_address == compatible_discovery.address
        assert session.scalar(select(func.count()).select_from(DeviceLifecycleEvent).where(
            DeviceLifecycleEvent.operation_id == f"{operation['operation_id']}-provision")) == 1


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


@pytest.mark.parametrize("failure", [TimeoutError("timed out"), BleProvisioningError("disconnected")])
def test_ble_identity_read_failure_blocks_provisioning(client, app, compatible_discovery, failure):
    operation = prepare_ble_workflow(client, app, compatible_discovery)

    class FailedProvisioner:
        async def read_onboarding_identity(self, _address):
            raise failure

    app.state.node_provisioner = FailedProvisioner()
    scanned = client.post(f"/api/reset-reregister/{operation['operation_id']}/scan-ble", json={})
    assert scanned.status_code == 200 and scanned.json()["state"] == "searching_for_reset_sensor_ble"
    candidate = scanned.json()["candidates"][0]
    assert candidate["verification_status"] == "read_failure" and not candidate["provisioning_allowed"]
    assert scanned.json()["target_ble_address"] is None

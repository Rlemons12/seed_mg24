import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import func, select

from gateway.app.models import DeviceLifecycleEvent, Reading, SensorInstallation
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.services.device_lifecycle_service import DeviceLifecycleService
from gateway.app.services.lifecycle_confirmation import LifecycleConfirmationStore


def register(client, discovery):
    response = client.post("/api/devices", json={
        "device_id": "MG24-0001", "display_name": "Boiler sensor", "discovery_address": discovery.address,
    })
    assert response.status_code == 201


def confirmation(client, operation):
    body = {"operation": operation, "device_id": "MG24-0001"}
    if operation == "restore":
        removed = client.get("/api/device-lifecycle/removed").json()[0]
        body.update(expected_hardware_id=removed["hardware_id"], expected_ble_address=removed["ble_address"])
    response = client.post("/api/device-lifecycle/confirm", json=body)
    assert response.status_code == 200
    return response.json()["confirmation_token"]


def execute(client, operation, token):
    body = {
        "operation": operation, "device_id": "MG24-0001", "confirmation_token": token,
    }
    if operation == "restore":
        removed = client.get("/api/device-lifecycle/removed").json()[0]
        body.update(expected_hardware_id=removed["hardware_id"], expected_ble_address=removed["ble_address"])
    return client.post("/api/device-lifecycle/execute", json=body)


def seed_history_and_installation(app):
    with app.state.session_factory() as session:
        device = DeviceRepository(session).get("MG24-0001")
        session.add(SensorInstallation(
            installation_id="install-1", node_id=device.device_id, device_id="ARM2001-01", display_name="Input",
            sensor_profile_id="generic-analog-raw", sensor_profile_version="1.0.0", interface_id="D0",
            enabled=True, provisioning_state="active", configuration_json="{}",
        ))
        session.add(Reading(
            registered_device_id=device.id, session_id="session", channel="analog_0", raw_value=12,
            payload_json=json.dumps({"value": 12}),
        ))
        session.commit()


def test_remove_online_sensor_archives_membership_preserves_history_and_audits(client, app, compatible_discovery):
    register(client, compatible_discovery)
    seed_history_and_installation(app)
    app.state.ble_manager.schedule("MG24-0001", compatible_discovery.address)
    response = execute(client, "remove", confirmation(client, "remove"))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "removed"
    assert body["telemetry_preserved"] is True and body["physical_sensor_changed"] is False
    assert "MG24-0001" not in app.state.ble_manager.connections
    with app.state.session_factory() as session:
        device = DeviceRepository(session).get("MG24-0001")
        installation = session.scalar(select(SensorInstallation).where(SensorInstallation.node_id == "MG24-0001"))
        assert device.archived and not device.enabled and device.lifecycle_state == "removed"
        assert installation.archived and not installation.enabled and installation.provisioning_state == "removed"
        assert session.scalar(select(func.count()).select_from(Reading)) == 1
        event = session.scalar(select(DeviceLifecycleEvent).where(DeviceLifecycleEvent.device_id == "MG24-0001"))
        assert event.event_type == "gateway_removed" and not event.factory_reset_requested


def test_removal_is_idempotent_and_confirmation_is_single_use(client, compatible_discovery):
    register(client, compatible_discovery)
    token = confirmation(client, "remove")
    assert execute(client, "remove", token).status_code == 200
    assert execute(client, "remove", token).status_code == 409
    second = execute(client, "remove", confirmation(client, "remove"))
    assert second.status_code == 200 and second.json()["already_applied"] is True


def test_confirmation_token_has_only_one_concurrent_consumer():
    store = LifecycleConfirmationStore()
    token = store.issue("remove", 7, "MG24-0001", "0x0123456789ABCDEF", "AA:BB")

    def consume():
        try:
            store.consume(token, "remove", 7, "MG24-0001", "0x0123456789ABCDEF", "AA:BB")
            return "accepted"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _index: consume(), range(2))) == ["accepted", "rejected"]


def test_expired_confirmation_is_consumed():
    store = LifecycleConfirmationStore(ttl_seconds=0)
    token = store.issue("remove", 7, "MG24-0001", None, "AA:BB")
    with pytest.raises(ValueError, match="expired"):
        store.consume(token, "remove", 7, "MG24-0001", None, "AA:BB")
    with pytest.raises(ValueError, match="already used"):
        store.consume(token, "remove", 7, "MG24-0001", None, "AA:BB")


def test_remove_offline_sensor(client, app, compatible_discovery):
    register(client, compatible_discovery)
    response = execute(client, "remove", confirmation(client, "remove"))
    assert response.status_code == 200
    with app.state.session_factory() as session:
        device = DeviceRepository(session).get("MG24-0001")
        assert device.archived and device.connection_status == "disabled"


def test_removal_rolls_back_when_database_commit_fails(client, app, compatible_discovery, monkeypatch):
    register(client, compatible_discovery)
    with app.state.session_factory() as session:
        original_commit = session.commit

        def fail_commit():
            raise RuntimeError("simulated database failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="simulated database failure"):
            DeviceLifecycleService(session).remove("MG24-0001", reason="test", connectivity_state="offline")
        monkeypatch.setattr(session, "commit", original_commit)
        session.expire_all()
        device = DeviceRepository(session).get("MG24-0001")
        assert not device.archived and device.enabled and device.lifecycle_state == "active"
        assert session.scalar(select(func.count()).select_from(DeviceLifecycleEvent)) == 0


def test_restore_requires_explicit_confirmed_operation_and_reuses_record(client, app, compatible_discovery):
    register(client, compatible_discovery)
    seed_history_and_installation(app)
    execute(client, "remove", confirmation(client, "remove"))
    assert client.get("/api/nodes").json() == []
    imported = client.post("/api/commissioning/nodes", json={
        "discovery_address": compatible_discovery.address, "node_id": "MG24-0001", "display_name": "Wrong path",
        "idempotency_key": "0123456789abcdef", "configuration": {
            "sample_interval_ms": 100, "processing_interval_ms": 100, "report_interval_ms": 100,
            "heartbeat_interval_ms": 30000, "filter_type": "none", "filter_window": 1,
        },
    })
    assert imported.status_code == 409 and imported.json()["detail"]["code"] == "device_removed"
    response = execute(client, "restore", confirmation(client, "restore"))
    assert response.status_code == 200 and response.json()["lifecycle_state"] == "active"
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Reading)) == 1
        assert session.scalar(select(func.count()).select_from(DeviceLifecycleEvent)) == 2


@pytest.mark.asyncio
async def test_removed_device_rejects_stale_telemetry(client, app, compatible_discovery):
    register(client, compatible_discovery)
    execute(client, "remove", confirmation(client, "remove"))
    with pytest.raises(ValueError, match="removed or disabled"):
        await app.state.ble_manager.telemetry_callback(
            "MG24-0001", b'{"t":"h","v":1,"s":1,"ms":1,"bv":4.0,"bu":0,"dr":0,"pe":0,"se":0}'
        )


def test_lifecycle_requires_json_and_same_origin(client, compatible_discovery):
    register(client, compatible_discovery)
    assert client.post("/api/device-lifecycle/confirm", content="{}").status_code == 415
    assert client.post(
        "/api/device-lifecycle/confirm", json={"operation": "remove", "device_id": "MG24-0001"},
        headers={"Origin": "https://evil.example"},
    ).status_code == 403
    assert client.post(
        "/api/device-lifecycle/confirm", json={"operation": "remove", "device_id": "MG24-0001"},
        headers={"Origin": ""},
    ).status_code == 403
    assert client.post(
        "/api/device-lifecycle/confirm", content=b"{}" * 3000,
        headers={"Content-Type": "application/json", "Origin": "http://testserver"},
    ).status_code == 413


def test_unknown_device_and_hardware_mismatch_are_rejected(client, app, compatible_discovery):
    assert client.post("/api/device-lifecycle/confirm", json={"operation": "remove", "device_id": "MISSING"}).status_code == 404
    register(client, compatible_discovery)
    with app.state.session_factory() as session:
        device = DeviceRepository(session).get("MG24-0001")
        device.hardware_id = "0x0123456789ABCDEF"
        session.commit()
    response = client.post("/api/device-lifecycle/confirm", json={
        "operation": "restore", "device_id": "MG24-0001", "expected_hardware_id": "0xFEDCBA9876543210",
    })
    assert response.status_code == 409


def test_restore_rejects_omitted_stored_identity(client, app, compatible_discovery):
    register(client, compatible_discovery)
    with app.state.session_factory() as session:
        device = DeviceRepository(session).get("MG24-0001")
        device.hardware_id = "0x0123456789ABCDEF"
        session.commit()
    execute(client, "remove", confirmation(client, "remove"))
    response = client.post("/api/device-lifecycle/confirm", json={"operation": "restore", "device_id": "MG24-0001"})
    assert response.status_code == 409


def test_dashboard_separates_remove_restore_and_factory_reset_workflows():
    template = Path("gateway/app/templates/index.html").read_text(encoding="utf-8")
    script = Path("gateway/app/static/app.js").read_text(encoding="utf-8")
    assert "Remove from network" in script and "Restore/Reapprove" in template + script
    assert "does not factory-reset" in script
    assert "Historical telemetry" in script
    assert 'id="lifecycle-confirm-id"' in template
    assert "/api/device-lifecycle/confirm" in script and "/api/device-lifecycle/execute" in script
    assert 'id="factory-reset-dialog"' in template
    assert "/api/factory-reset/" in script and "USB is required" in template
    assert "Remove from network" in script and "Factory Reset Sensor" in script

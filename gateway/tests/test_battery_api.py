from fastapi.testclient import TestClient
from sqlalchemy import select

from gateway.app.models import BatteryGeneration, RegisteredDevice


def add_device(app):
    with app.state.session_factory() as session:
        session.add(RegisteredDevice(device_id="BAT-API-1", display_name="Battery API fixture"))
        session.commit()


def test_battery_summary_starts_unknown_without_fabricated_percentage(client, app):
    add_device(app)
    response = client.get("/api/devices/BAT-API-1/battery")
    assert response.status_code == 200
    assert response.json()["voltage"]["percentage"] is None
    assert response.json()["voltage"]["trend"]["recent_sample_count"] == 0
    assert response.json()["health"]["status"] == "LEARNING"
    assert response.json()["prediction"]["confidence"] == "UNKNOWN"


def test_manual_charge_cycle_history_and_detail(client, app):
    add_device(app)
    charged = client.post("/api/devices/BAT-API-1/battery/mark-charged", json={"notes": "fixture charge"})
    assert charged.status_code == 201
    cycles = client.get("/api/devices/BAT-API-1/battery/cycles")
    assert cycles.status_code == 200
    assert len(cycles.json()) == 1
    detail = client.get(f"/api/devices/BAT-API-1/battery/cycles/{charged.json()['cycle_id']}")
    assert detail.status_code == 200
    assert detail.json()["start_reason"] == "MANUAL_CHARGE"


def test_battery_replacement_preserves_identity_and_old_generation(client, app):
    add_device(app)
    client.post("/api/devices/BAT-API-1/battery/mark-charged", json={})
    response = client.post(
        "/api/devices/BAT-API-1/battery/replace",
        json={"reason": "scheduled fixture replacement", "notes": "synthetic test"},
    )
    assert response.status_code == 201
    history = client.get("/api/devices/BAT-API-1/battery/history")
    assert len(history.json()["replacements"]) == 1
    with app.state.session_factory() as session:
        device = session.scalar(select(RegisteredDevice).where(RegisteredDevice.device_id == "BAT-API-1"))
        generations = list(session.scalars(select(BatteryGeneration).where(
            BatteryGeneration.registered_device_id == device.id,
        )))
        assert device.device_id == "BAT-API-1"
        assert len(generations) == 2


def test_battery_mutations_validate_requests_and_require_same_origin(app):
    add_device(app)
    with TestClient(app) as anonymous:
        blocked = anonymous.post("/api/devices/BAT-API-1/battery/mark-charged", json={})
    assert blocked.status_code == 403
    with TestClient(app, headers={"Origin": "http://testserver"}) as allowed:
        invalid = allowed.post("/api/devices/BAT-API-1/battery/replace", json={"reason": ""})
    assert invalid.status_code == 422


def test_missing_device_and_cycle_are_not_found(client, app):
    assert client.get("/api/devices/UNKNOWN/battery").status_code == 404
    add_device(app)
    assert client.get("/api/devices/BAT-API-1/battery/cycles/999").status_code == 404

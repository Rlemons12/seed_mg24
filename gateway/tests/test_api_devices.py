from datetime import UTC


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200 and response.json()["status"] == "ok"


def test_dashboard_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Sensor Monitoring" in response.text


def test_register_update_and_duplicate(client, compatible_discovery):
    body = {"device_id": "ARM2001-01", "display_name": "Boiler room", "discovery_address": compatible_discovery.address}
    response = client.post("/api/devices", json=body)
    assert response.status_code == 201
    assert response.json()["device_id"] == "ARM2001-01"
    updated = client.patch("/api/devices/ARM2001-01", json={"display_name": "West wall"})
    assert updated.status_code == 200 and updated.json()["device_id"] == "ARM2001-01"
    duplicate = client.post("/api/devices", json={**body, "discovery_address": compatible_discovery.address})
    assert duplicate.status_code == 409


def test_incompatible_requires_override(client, app):
    from datetime import datetime

    from gateway.app.schemas import Discovery

    item = Discovery(
        address="BB",
        name="XIAO-MG24-Sense",
        compatible=False,
        compatibility_reason="name only",
        last_seen_at=datetime.now(UTC),
    )
    app.state.scanner.record(item)
    body = {"device_id": "ARM2001-02", "display_name": "Node", "discovery_address": "BB"}
    assert client.post("/api/devices", json=body).status_code == 422
    assert client.post("/api/devices", json={**body, "allow_incompatible": True}).status_code == 201


def test_history_pagination_limits(client, compatible_discovery):
    client.post(
        "/api/devices",
        json={"device_id": "ARM2001-01", "display_name": "Node", "discovery_address": compatible_discovery.address},
    )
    response = client.get("/api/devices/ARM2001-01/readings?limit=501")
    assert response.status_code == 422


def test_structured_error(client):
    response = client.get("/api/devices/MISSING")
    assert response.status_code == 404 and response.json()["error"] == "request_error"


def test_command_endpoint_rejects_non_allowlisted_input(client, compatible_discovery):
    client.post(
        "/api/devices",
        json={"device_id": "ARM2001-01", "display_name": "Node", "discovery_address": compatible_discovery.address},
    )
    response = client.post("/api/devices/ARM2001-01/commands", json={"command": "BLE START"})
    assert response.status_code == 422


def test_command_endpoint_requires_same_origin(client, compatible_discovery):
    client.post(
        "/api/devices",
        json={"device_id": "ARM2001-01", "display_name": "Node", "discovery_address": compatible_discovery.address},
    )
    response = client.post(
        "/api/devices/ARM2001-01/commands",
        json={"command": "MODE LIVE"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403

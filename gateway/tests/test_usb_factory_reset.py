import asyncio
from types import SimpleNamespace

import pytest

from gateway.app.services.usb_factory_reset import UsbFactoryResetService, UsbResetError


class FakeClient:
    state = {
        "hardware_id": "0x0123456789ABCDEF", "node_id": "MG24-0001", "identity_status": "ok",
        "provisioning_state": "provisioned", "firmware_version": "0.1.0",
    }

    def __init__(self, port, timeout=3):
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def request(self, action, **fields):
        if action == "read_node_state":
            return {"result": dict(self.state)}
        if action == "prepare_factory_reset":
            assert fields["reset_protocol_version"] == 2
            return {"result": {"hardware_id": self.state["hardware_id"], "operation_id": "A" * 32, "challenge": "B" * 32}}
        if action == "confirm_factory_reset":
            type(self).state = {
                **type(self).state,
                "node_id": None,
                "identity_status": "unprovisioned",
                "provisioning_state": "unprovisioned",
            }
            return {"result": {"operation_id": "A" * 32}}
        raise AssertionError(action)


def ports():
    return [SimpleNamespace(device="COM9", serial_number="ABCDEF12", vid=0x2886, pid=0x0062)]


@pytest.mark.asyncio
async def test_usb_reset_prepare_requires_exact_physical_identity_and_consumes_confirmation():
    FakeClient.state = {**FakeClient.state, "node_id": "MG24-0001", "identity_status": "ok"}
    service = UsbFactoryResetService(FakeClient, ports, reboot_timeout=1)
    token, state = service.prepare("MG24-0001", "0x0123456789ABCDEF", "COM9")
    assert state["port"] == "COM9" and state["hardware_id"] == "0x0123456789ABCDEF"
    operation = service.start(token, "MG24-0001", "0x0123456789ABCDEF", "COM9")
    assert operation.device_id == "MG24-0001"
    with pytest.raises(UsbResetError, match="missing, expired, reused"):
        service.start(token, "MG24-0001", "0x0123456789ABCDEF", "COM9")
    for _ in range(10):
        if operation.physical_reset_complete:
            break
        await asyncio.sleep(0.1)
    assert operation.physical_reset_complete and operation.state == "physical_complete"


def test_usb_reset_rejects_wrong_sensor_and_ambiguous_port():
    service = UsbFactoryResetService(FakeClient, ports)
    with pytest.raises(UsbResetError, match="does not match"):
        service.prepare("MG24-9999", "0x0123456789ABCDEF", "COM9")
    with pytest.raises(UsbResetError, match="exactly one"):
        service.prepare("MG24-0001", "0x0123456789ABCDEF", "COM8")


def test_factory_reset_api_is_loopback_same_origin_and_hardware_bound(client, app, compatible_discovery):
    FakeClient.state = {
        **FakeClient.state,
        "node_id": "MG24-0001",
        "identity_status": "ok",
        "provisioning_state": "provisioned",
    }
    client.post("/api/devices", json={
        "device_id": "MG24-0001", "display_name": "Node", "discovery_address": compatible_discovery.address,
    })
    app.state.usb_factory_reset = UsbFactoryResetService(FakeClient, ports, reboot_timeout=1)
    body = {"device_id": "MG24-0001", "hardware_id": "0x0123456789ABCDEF", "port": "COM9"}
    assert client.post("/api/factory-reset/confirm", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    response = client.post("/api/factory-reset/confirm", json=body)
    assert response.status_code == 200 and response.json()["hardware_id"] == body["hardware_id"]
    wrong = client.post("/api/factory-reset/execute", json={**body, "hardware_id": "0xFEDCBA9876543210",
                                                            "confirmation_token": response.json()["confirmation_token"]})
    assert wrong.status_code == 409

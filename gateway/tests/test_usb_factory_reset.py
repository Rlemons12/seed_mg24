import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from gateway.app.services.usb_factory_reset import UsbFactoryResetService, UsbResetError
from sensor_package.tools.bootstrap.protocol import ProtocolError


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


def changing_ports():
    port = "COM10" if FakeClient.state.get("identity_status") == "unprovisioned" else "COM9"
    return [SimpleNamespace(device=port, serial_number="ABCDEF12", vid=0x2886, pid=0x0062)]


@pytest.mark.asyncio
async def test_usb_reset_prepare_requires_exact_physical_identity_and_consumes_confirmation():
    FakeClient.state = {**FakeClient.state, "node_id": "MG24-0001", "identity_status": "ok"}
    service = UsbFactoryResetService(FakeClient, changing_ports, reboot_timeout=1)
    token, state = service.prepare(1, "MG24-0001", "0x0123456789ABCDEF", "COM9")
    assert state["port"] == "COM9" and state["hardware_id"] == "0x0123456789ABCDEF"
    operation = service.start(token, 1, "MG24-0001", "0x0123456789ABCDEF", "COM9")
    assert operation.device_id == "MG24-0001"
    with pytest.raises(UsbResetError, match="missing, expired, reused"):
        service.start(token, 1, "MG24-0001", "0x0123456789ABCDEF", "COM9")
    for _ in range(10):
        if operation.physical_reset_complete:
            break
        await asyncio.sleep(0.1)
    assert operation.physical_reset_complete and operation.state == "physical_complete"
    assert operation.post_reset["port"] == "COM10"


def test_usb_reset_cancellation_consumes_hardware_bound_confirmation():
    FakeClient.state = {
        **FakeClient.state, "node_id": "MG24-0001", "identity_status": "ok", "provisioning_state": "provisioned",
    }
    service = UsbFactoryResetService(FakeClient, ports)
    token, _state = service.prepare(7, "MG24-0001", "0x0123456789ABCDEF", "COM9")
    service.cancel(token, 7, "MG24-0001", "0x0123456789ABCDEF", "COM9")
    with pytest.raises(UsbResetError, match="missing, expired, reused"):
        service.start(token, 7, "MG24-0001", "0x0123456789ABCDEF", "COM9")


def test_usb_reset_rejects_wrong_sensor_and_ambiguous_port():
    service = UsbFactoryResetService(FakeClient, ports)
    with pytest.raises(UsbResetError, match="does not match"):
        service.prepare(1, "MG24-9999", "0x0123456789ABCDEF", "COM9")
    with pytest.raises(UsbResetError, match="exactly one"):
        service.prepare(1, "MG24-0001", "0x0123456789ABCDEF", "COM8")


def test_usb_inspection_retries_transient_protocol_timeout():
    class FlakyClient(FakeClient):
        attempts = 0

        def request(self, action, **fields):
            type(self).attempts += 1
            if type(self).attempts == 1:
                raise ProtocolError("bootstrap response timeout")
            return super().request(action, **fields)

    service = UsbFactoryResetService(FlakyClient, ports)

    assert service.inspect("COM9")["hardware_id"] == "0x0123456789ABCDEF"
    assert FlakyClient.attempts == 2


def test_usb_inspection_normalizes_persistent_protocol_timeout():
    class TimeoutClient(FakeClient):
        def request(self, _action, **_fields):
            raise ProtocolError("bootstrap response timeout")

    service = UsbFactoryResetService(TimeoutClient, ports)

    with pytest.raises(UsbResetError, match="identity read failed after retry"):
        service.inspect("COM9")


def test_usb_inspection_serializes_access_to_the_same_port():
    class ExclusiveClient(FakeClient):
        active = 0
        maximum_active = 0

        def __enter__(self):
            type(self).active += 1
            type(self).maximum_active = max(type(self).maximum_active, type(self).active)
            time.sleep(0.05)
            return self

        def __exit__(self, *_):
            type(self).active -= 1

    service = UsbFactoryResetService(ExclusiveClient, ports)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(service.inspect, ("COM9", "COM9")))

    assert all(result["hardware_id"] == "0x0123456789ABCDEF" for result in results)
    assert ExclusiveClient.maximum_active == 1


@pytest.mark.asyncio
async def test_reset_retries_only_non_destructive_challenge_preparation():
    class FlakyPrepareClient(FakeClient):
        prepare_attempts = 0
        confirm_attempts = 0

        def request(self, action, **fields):
            if action == "prepare_factory_reset":
                type(self).prepare_attempts += 1
                if type(self).prepare_attempts == 1:
                    raise ProtocolError("bootstrap response timeout")
            if action == "confirm_factory_reset":
                type(self).confirm_attempts += 1
            return super().request(action, **fields)

    FakeClient.state = {
        **FakeClient.state, "node_id": "MG24-0001", "identity_status": "ok", "provisioning_state": "provisioned",
    }
    service = UsbFactoryResetService(FlakyPrepareClient, ports, reboot_timeout=1)
    token, _ = service.prepare(1, "MG24-0001", "0x0123456789ABCDEF", "COM9")
    operation = service.start(token, 1, "MG24-0001", "0x0123456789ABCDEF", "COM9")
    for _ in range(15):
        if operation.state in {"physical_complete", "failed"}:
            break
        await asyncio.sleep(0.1)

    assert operation.state == "physical_complete"
    assert FlakyPrepareClient.prepare_attempts == 2
    assert FlakyPrepareClient.confirm_attempts == 1
    assert "retrying_device_reset_challenge" in operation.progress


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
    assert client.get("/api/factory-reset/boards", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 403

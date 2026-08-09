import json

import pytest

from gateway.app.ble.constants import CAPABILITIES_UUID, METADATA_UUID, ONBOARDING_IDENTITY_UUID, TELEMETRY_UUID
from gateway.app.ble.onboarding_identity import derive_onboarding_identity
from gateway.app.services.ble_provisioning import BleNodeProvisioner, BleProvisioningError


class FakeClient:
    def __init__(self, _address, *, node_id="UNASSIGNED-MG24", mismatch=False):
        self.node_id = node_id
        self.mismatch = mismatch
        self.callback = None
        self.values = [100, 100, 100, 30000, 2, 2, 1]

    async def __aenter__(self): return self
    async def __aexit__(self, *_): return None
    async def read_gatt_char(self, uuid):
        if uuid == METADATA_UUID:
            return json.dumps({"node_id": self.node_id, "protocol_version": "1.0.0"}).encode()
        if uuid == CAPABILITIES_UUID:
            return json.dumps({"configuration": {"readback": True}}).encode()
        if uuid == ONBOARDING_IDENTITY_UUID:
            payload = {"schema_version": 1, "provisioning_state": "provisioned", "protocol_version": "1.0.0"}
            if self.node_id == "UNASSIGNED-MG24":
                payload.update(onboarding_identity=derive_onboarding_identity("0x0123456789ABCDEF"),
                               provisioning_state="unprovisioned", firmware_version="0.1.0")
            return json.dumps(payload).encode()
        raise AssertionError(uuid)
    async def start_notify(self, uuid, callback): assert uuid == TELEMETRY_UUID; self.callback = callback
    async def stop_notify(self, uuid): assert uuid == TELEMETRY_UUID
    async def write_gatt_char(self, _uuid, data, response):
        assert response and len(data) <= 64
        parts = data.decode().split()
        tx = parts[2]
        if parts[0] == "PROV":
            self.node_id = parts[3]
            payload = {"t": "ca", "v": 1, "id": self.node_id, "tx": tx, "code": "provisioned"}
        elif parts[0] == "CFGSET":
            self.values = [int(value) for value in parts[3:10]]
            payload = {"t": "ca", "v": 1, "id": self.node_id, "tx": tx, "code": "configured"}
        else:
            payload = {"t": "ca", "v": 1, "id": "WRONG" if self.mismatch else self.node_id, "tx": tx,
                       "code": "readback", "sample": self.values[0], "process": self.values[1],
                       "report": self.values[2], "heartbeat": self.values[3], "filter": self.values[4],
                       "window": self.values[5], "enabled": self.values[6]}
        self.callback(None, bytearray(json.dumps(payload).encode()))


CONFIG = {"sample_interval_ms": 100, "processing_interval_ms": 100, "report_interval_ms": 100,
          "heartbeat_interval_ms": 30000, "filter_type": "ema", "filter_window": 2}


@pytest.mark.asyncio
async def test_real_ble_transaction_provisions_and_verifies_readback():
    client = FakeClient("address")
    result = await BleNodeProvisioner(lambda _: client).provision("address", "MG24-0001", "abcdef0123456789", CONFIG)
    assert result["readback"]["id"] == "MG24-0001"


@pytest.mark.asyncio
async def test_hardware_bound_provisioning_verifies_identity_before_and_hides_it_after():
    client = FakeClient("address")
    expected = derive_onboarding_identity("0x0123456789ABCDEF")
    result = await BleNodeProvisioner(lambda _: client).provision(
        "address", "MG24-0001", "abcdef0123456789", CONFIG, expected_onboarding_identity=expected)
    assert result["onboarding_after"] == {
        "schema_version": 1, "provisioning_state": "provisioned", "protocol_version": "1.0.0"}


@pytest.mark.asyncio
async def test_hardware_bound_provisioning_rejects_mismatch_before_write():
    client = FakeClient("address")
    with pytest.raises(BleProvisioningError, match="no longer matches"):
        await BleNodeProvisioner(lambda _: client).provision(
            "address", "MG24-0001", "abcdef0123456789", CONFIG,
            expected_onboarding_identity="0" * 32)
    assert client.node_id == "UNASSIGNED-MG24"


@pytest.mark.asyncio
async def test_real_ble_transaction_rejects_readback_mismatch():
    client = FakeClient("address", mismatch=True)
    with pytest.raises(BleProvisioningError, match="readback"):
        await BleNodeProvisioner(lambda _: client).provision("address", "MG24-0001", "abcdef0123456789", CONFIG)


@pytest.mark.asyncio
async def test_assigned_identity_can_resume_readback_but_not_change_identity():
    client = FakeClient("address", node_id="MG24-0001")
    await BleNodeProvisioner(lambda _: client).provision("address", "MG24-0001", "abcdef0123456789", CONFIG)
    with pytest.raises(BleProvisioningError, match="different identity"):
        await BleNodeProvisioner(lambda _: client).provision("address", "MG24-0002", "abcdef0123456789", CONFIG)


@pytest.mark.asyncio
async def test_read_state_uses_correlated_readback_as_authoritative_identity():
    client = FakeClient("address", node_id="MG24-0002")
    state = await BleNodeProvisioner(lambda _: client).read_state("address")
    assert state["readback"]["id"] == "MG24-0002"
    assert state["readback"]["code"] == "readback"


@pytest.mark.asyncio
async def test_assigned_device_configuration_uses_atomic_command_and_readback():
    client = FakeClient("address", node_id="MG24-0002")
    changed = {**CONFIG, "report_interval_ms": 200}
    result = await BleNodeProvisioner(lambda _: client).configure(
        "address", "MG24-0002", "abcdef0123456789", changed
    )
    assert result["acknowledgement"]["code"] == "configured"
    assert result["readback"]["id"] == "MG24-0002"

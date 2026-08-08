import json

import pytest

from gateway.app.ble.constants import CAPABILITIES_UUID, METADATA_UUID, TELEMETRY_UUID
from gateway.app.services.ble_provisioning import BleNodeProvisioner, BleProvisioningError


class FakeClient:
    def __init__(self, _address, *, node_id="UNASSIGNED-MG24", mismatch=False):
        self.node_id = node_id
        self.mismatch = mismatch
        self.callback = None

    async def __aenter__(self): return self
    async def __aexit__(self, *_): return None
    async def read_gatt_char(self, uuid):
        if uuid == METADATA_UUID:
            return json.dumps({"node_id": self.node_id, "protocol_version": "1.0.0"}).encode()
        if uuid == CAPABILITIES_UUID:
            return json.dumps({"configuration": {"readback": True}}).encode()
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
        else:
            payload = {"t": "ca", "v": 1, "id": "WRONG" if self.mismatch else self.node_id, "tx": tx,
                       "code": "readback", "sample": 100, "process": 100, "report": 100, "heartbeat": 30000,
                       "filter": 2, "window": 2, "enabled": 1}
        self.callback(None, bytearray(json.dumps(payload).encode()))


CONFIG = {"sample_interval_ms": 100, "processing_interval_ms": 100, "report_interval_ms": 100,
          "heartbeat_interval_ms": 30000, "filter_type": "ema", "filter_window": 2}


@pytest.mark.asyncio
async def test_real_ble_transaction_provisions_and_verifies_readback():
    client = FakeClient("address")
    result = await BleNodeProvisioner(lambda _: client).provision("address", "MG24-0001", "abcdef0123456789", CONFIG)
    assert result["readback"]["id"] == "MG24-0001"


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

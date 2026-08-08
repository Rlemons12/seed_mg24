import asyncio
import json
import re
from collections.abc import Callable
from uuid import uuid4

from gateway.app.ble.constants import CAPABILITIES_UUID, COMMAND_UUID, METADATA_UUID, TELEMETRY_UUID


class BleProvisioningError(ValueError):
    pass


class BleNodeProvisioner:
    """Bounded write-once onboarding over the production command characteristic."""

    FILTERS = {"none": 0, "moving_average": 1, "ema": 2, "median": 3}

    def __init__(self, client_factory: Callable | None = None, timeout_seconds: float = 15.0) -> None:
        self.client_factory = client_factory
        self.timeout_seconds = timeout_seconds
        self._locks: dict[str, asyncio.Lock] = {}

    def _client(self, address: str):
        if self.client_factory is None:
            from bleak import BleakClient

            return BleakClient(address)
        return self.client_factory(address)

    async def inspect(self, address: str) -> tuple[dict, dict]:
        async with self._client(address) as client:
            metadata = json.loads(bytes(await client.read_gatt_char(METADATA_UUID)).decode("utf-8"))
            capabilities = json.loads(bytes(await client.read_gatt_char(CAPABILITIES_UUID)).decode("utf-8"))
        return metadata, capabilities

    async def read_state(self, address: str) -> dict:
        """Read authoritative identity/configuration without changing persistent state."""
        transaction_id = uuid4().hex[:16].upper()
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=4)

        def notification(_sender, data: bytearray) -> None:
            try:
                payload = json.loads(bytes(data).decode("utf-8"))
                if payload.get("t") in {"ca", "ce"} and payload.get("tx") == transaction_id:
                    queue.put_nowait(payload)
            except (UnicodeDecodeError, json.JSONDecodeError, asyncio.QueueFull):
                return

        lock = self._locks.setdefault(address, asyncio.Lock())
        if lock.locked():
            raise BleProvisioningError("a provisioning operation is already active for this node")
        async with lock:
            async with self._client(address) as client:
                metadata = json.loads(bytes(await client.read_gatt_char(METADATA_UUID)).decode("utf-8").strip("\x00"))
                capabilities = json.loads(
                    bytes(await client.read_gatt_char(CAPABILITIES_UUID)).decode("utf-8").strip("\x00")
                )
                await client.start_notify(TELEMETRY_UUID, notification)
                await client.write_gatt_char(
                    COMMAND_UUID, f"PROVGET 1 {transaction_id}".encode("ascii"), response=True
                )
                readback = await asyncio.wait_for(queue.get(), timeout=self.timeout_seconds)
                await client.stop_notify(TELEMETRY_UUID)
        if readback.get("code") != "readback" or not isinstance(readback.get("id"), str):
            raise BleProvisioningError("device returned an invalid provisioning state")
        return {"metadata": metadata, "capabilities": capabilities, "readback": readback}

    async def provision(self, address: str, node_id: str, transaction_id: str, configuration: dict) -> dict:
        if not re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+)*", node_id) or len(node_id) > 31:
            raise BleProvisioningError("node_id is invalid")
        if not re.fullmatch(r"[0-9a-f]{16,24}", transaction_id):
            raise BleProvisioningError("transaction_id is invalid")
        transaction_id = transaction_id.upper()
        lock = self._locks.setdefault(address, asyncio.Lock())
        if lock.locked():
            raise BleProvisioningError("a provisioning operation is already active for this node")
        async with lock:
            return await asyncio.wait_for(
                self._provision(address, node_id, transaction_id, configuration), timeout=self.timeout_seconds
            )

    async def _provision(self, address: str, node_id: str, transaction_id: str, configuration: dict) -> dict:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=16)

        def notification(_sender, data: bytearray) -> None:
            try:
                payload = json.loads(bytes(data).decode("utf-8"))
                if payload.get("t") in {"ca", "ce"}:
                    queue.put_nowait(payload)
            except (UnicodeDecodeError, json.JSONDecodeError, asyncio.QueueFull):
                return

        filter_value = self.FILTERS.get(configuration["filter_type"])
        if filter_value is None:
            raise BleProvisioningError("unsupported filter type")
        values = (
            configuration["sample_interval_ms"],
            configuration["processing_interval_ms"],
            configuration["report_interval_ms"],
            configuration["heartbeat_interval_ms"],
            filter_value,
            configuration["filter_window"],
            1,
        )
        command = f"PROV 1 {transaction_id} {node_id} " + " ".join(str(value) for value in values)
        if len(command.encode("ascii")) > 191:
            raise BleProvisioningError("provisioning command exceeds firmware limit")
        async with self._client(address) as client:
            metadata = json.loads(bytes(await client.read_gatt_char(METADATA_UUID)).decode("utf-8"))
            capabilities = json.loads(bytes(await client.read_gatt_char(CAPABILITIES_UUID)).decode("utf-8"))
            if metadata.get("protocol_version") != "1.0.0":
                raise BleProvisioningError("node protocol version is unsupported")
            if not capabilities.get("configuration", {}).get("readback"):
                raise BleProvisioningError("node does not support persistent configuration readback")
            await client.start_notify(TELEMETRY_UUID, notification)
            if metadata.get("node_id") == "UNASSIGNED-MG24":
                await client.write_gatt_char(COMMAND_UUID, command.encode("ascii"), response=True)
                acknowledgement = await self._wait_for(queue, transaction_id, {"provisioned", "already_committed"})
            elif metadata.get("node_id") == node_id:
                acknowledgement = {"t": "ca", "v": 1, "id": node_id, "tx": transaction_id, "code": "resume_readback"}
            else:
                raise BleProvisioningError("node is already assigned to a different identity")
            await client.write_gatt_char(COMMAND_UUID, f"PROVGET 1 {transaction_id}".encode("ascii"), response=True)
            readback = await self._wait_for(queue, transaction_id, {"readback"})
            await client.stop_notify(TELEMETRY_UUID)
        expected = {
            "sample": values[0], "process": values[1], "report": values[2], "heartbeat": values[3],
            "filter": values[4], "window": values[5], "enabled": values[6],
        }
        if readback.get("id") != node_id or any(readback.get(key) != value for key, value in expected.items()):
            raise BleProvisioningError("device readback does not match requested identity and configuration")
        return {"acknowledgement": acknowledgement, "readback": readback, "metadata": metadata, "capabilities": capabilities}

    async def _wait_for(self, queue: asyncio.Queue[dict], transaction_id: str, accepted_codes: set[str]) -> dict:
        while True:
            result = await asyncio.wait_for(queue.get(), timeout=self.timeout_seconds)
            if result.get("tx") != transaction_id:
                continue
            if result.get("t") == "ce":
                raise BleProvisioningError(str(result.get("code", "device_rejected")))
            if result.get("code") in accepted_codes:
                return result

import asyncio
import json
import re
from collections.abc import Callable
from uuid import uuid4

from gateway.app.ble.constants import (
    CAPABILITIES_UUID,
    COMMAND_UUID,
    METADATA_UUID,
    ONBOARDING_IDENTITY_UUID,
    TELEMETRY_UUID,
)
from gateway.app.ble.onboarding_identity import OnboardingIdentityError, parse_onboarding_payload


class BleProvisioningError(ValueError):
    pass


class BleNodeProvisioner:
    """Canonical bounded adapter for authoritative identity and device configuration."""

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

    async def read_onboarding_identity(self, address: str) -> dict:
        """Read the correlation-only bootstrap identity without changing sensor state."""
        try:
            async with self._client(address) as client:
                raw = bytes(await client.read_gatt_char(ONBOARDING_IDENTITY_UUID))
            return parse_onboarding_payload(raw)
        except OnboardingIdentityError:
            raise
        except Exception as exc:
            raise BleProvisioningError(f"onboarding identity read failed: {exc}") from exc

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

    async def provision(self, address: str, node_id: str, transaction_id: str, configuration: dict,
                        expected_onboarding_identity: str | None = None) -> dict:
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
                self._provision(address, node_id, transaction_id, configuration, expected_onboarding_identity),
                timeout=self.timeout_seconds,
            )

    async def configure(self, address: str, node_id: str, transaction_id: str, configuration: dict) -> dict:
        """Atomically replace the one device-level persisted processing configuration."""
        if not re.fullmatch(r"[0-9a-f]{16,24}", transaction_id):
            raise BleProvisioningError("transaction_id is invalid")
        transaction_id = transaction_id.upper()
        values = self._configuration_values(configuration)
        command = f"CFGSET 1 {transaction_id} " + " ".join(str(value) for value in values)
        if len(command.encode("ascii")) > 191:
            raise BleProvisioningError("configuration command exceeds firmware limit")
        lock = self._locks.setdefault(address, asyncio.Lock())
        if lock.locked():
            raise BleProvisioningError("another dashboard operation is already using this sensor")
        async with lock:
            return await asyncio.wait_for(
                self._configure(address, node_id, transaction_id, command, values), timeout=self.timeout_seconds
            )

    async def _configure(self, address: str, node_id: str, transaction_id: str, command: str, values: tuple) -> dict:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=16)

        def notification(_sender, data: bytearray) -> None:
            try:
                payload = json.loads(bytes(data).decode("utf-8"))
                if payload.get("t") in {"ca", "ce"}:
                    queue.put_nowait(payload)
            except (UnicodeDecodeError, json.JSONDecodeError, asyncio.QueueFull):
                return

        async with self._client(address) as client:
            await client.start_notify(TELEMETRY_UUID, notification)
            await client.write_gatt_char(COMMAND_UUID, f"PROVGET 1 {transaction_id}".encode("ascii"), response=True)
            before = await self._wait_for(queue, transaction_id, {"readback"})
            if before.get("id") != node_id:
                raise BleProvisioningError("authoritative identity changed; no configuration was written")
            if self._readback_matches(before, values):
                await client.stop_notify(TELEMETRY_UUID)
                return {"acknowledgement": {"code": "already_applied", "tx": transaction_id}, "readback": before}
            await client.write_gatt_char(COMMAND_UUID, command.encode("ascii"), response=True)
            acknowledgement = await self._wait_for(queue, transaction_id, {"configured"})
            await client.write_gatt_char(COMMAND_UUID, f"PROVGET 1 {transaction_id}".encode("ascii"), response=True)
            readback = await self._wait_for(queue, transaction_id, {"readback"})
            await client.stop_notify(TELEMETRY_UUID)
        if not self._readback_matches(readback, values):
            raise BleProvisioningError("device configuration readback does not match the requested values")
        return {"acknowledgement": acknowledgement, "readback": readback}

    def _configuration_values(self, configuration: dict) -> tuple:
        filter_value = self.FILTERS.get(configuration["filter_type"])
        if filter_value is None:
            raise BleProvisioningError("unsupported filter type")
        return (
            configuration["sample_interval_ms"], configuration["processing_interval_ms"],
            configuration["report_interval_ms"], configuration["heartbeat_interval_ms"],
            filter_value, configuration["filter_window"], 1 if configuration.get("enabled", True) else 0,
        )

    @staticmethod
    def _readback_matches(readback: dict, values: tuple) -> bool:
        expected = dict(zip(("sample", "process", "report", "heartbeat", "filter", "window", "enabled"), values, strict=True))
        return all(readback.get(key) == value for key, value in expected.items())

    async def _provision(self, address: str, node_id: str, transaction_id: str, configuration: dict,
                         expected_onboarding_identity: str | None) -> dict:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=16)

        def notification(_sender, data: bytearray) -> None:
            try:
                payload = json.loads(bytes(data).decode("utf-8"))
                if payload.get("t") in {"ca", "ce"}:
                    queue.put_nowait(payload)
            except (UnicodeDecodeError, json.JSONDecodeError, asyncio.QueueFull):
                return

        values = self._configuration_values(configuration)
        command = f"PROV 1 {transaction_id} {node_id} " + " ".join(str(value) for value in values)
        if len(command.encode("ascii")) > 191:
            raise BleProvisioningError("provisioning command exceeds firmware limit")
        async with self._client(address) as client:
            if expected_onboarding_identity is not None:
                try:
                    onboarding_before = parse_onboarding_payload(
                        bytes(await client.read_gatt_char(ONBOARDING_IDENTITY_UUID)))
                except OnboardingIdentityError as exc:
                    raise BleProvisioningError(str(exc)) from exc
                if onboarding_before.get("onboarding_identity") != expected_onboarding_identity:
                    raise BleProvisioningError("BLE candidate no longer matches the USB-verified physical sensor")
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
            onboarding_after = None
            if expected_onboarding_identity is not None:
                try:
                    onboarding_after = parse_onboarding_payload(
                        bytes(await client.read_gatt_char(ONBOARDING_IDENTITY_UUID)))
                except OnboardingIdentityError as exc:
                    raise BleProvisioningError(str(exc)) from exc
            await client.stop_notify(TELEMETRY_UUID)
        expected = {
            "sample": values[0], "process": values[1], "report": values[2], "heartbeat": values[3],
            "filter": values[4], "window": values[5], "enabled": values[6],
        }
        if readback.get("id") != node_id or any(readback.get(key) != value for key, value in expected.items()):
            raise BleProvisioningError("device readback does not match requested identity and configuration")
        if expected_onboarding_identity is not None and (
            onboarding_after.get("provisioning_state") != "provisioned"
            or "onboarding_identity" in onboarding_after
        ):
            raise BleProvisioningError("bootstrap identity remained exposed after provisioning")
        return {"acknowledgement": acknowledgement, "readback": readback, "metadata": metadata,
                "capabilities": capabilities, "onboarding_after": onboarding_after}

    async def _wait_for(self, queue: asyncio.Queue[dict], transaction_id: str, accepted_codes: set[str]) -> dict:
        while True:
            result = await asyncio.wait_for(queue.get(), timeout=self.timeout_seconds)
            if result.get("tx") != transaction_id:
                continue
            if result.get("t") == "ce":
                raise BleProvisioningError(str(result.get("code", "device_rejected")))
            if result.get("code") in accepted_codes:
                return result

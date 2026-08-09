import asyncio
import secrets
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from uuid import uuid4

from serial.tools import list_ports

from sensor_package.tools.bootstrap.protocol import HARDWARE_ID_PATTERN, ProtocolError
from sensor_package.tools.bootstrap.serial_client import BootstrapSerialClient


class UsbResetError(ValueError):
    pass


@dataclass
class UsbResetOperation:
    record_id: int
    operation_id: str
    device_id: str
    hardware_id: str
    port: str
    state: str = "queued"
    progress: list[str] = field(default_factory=list)
    error: str | None = None
    physical_reset_complete: bool = False
    gateway_cleanup_complete: bool = False
    post_reset: dict | None = None


class UsbFactoryResetService:
    VID, PID = 0x2886, 0x0062

    def __init__(self, client_factory=BootstrapSerialClient, ports_factory=None, reboot_timeout: float = 30.0) -> None:
        self.client_factory = client_factory
        self.ports_factory = ports_factory or list_ports.comports
        self.reboot_timeout = reboot_timeout
        self.operations: dict[str, UsbResetOperation] = {}
        self._confirmations: dict[str, tuple[int, str, str, str, str, float]] = {}
        self._confirmation_lock = Lock()
        self._locks: dict[str, asyncio.Lock] = {}

    def ports(self) -> list[dict]:
        return [
            {"port": item.device, "usb_serial": item.serial_number, "usb_vid_pid": "2886:0062"}
            for item in self.ports_factory() if item.vid == self.VID and item.pid == self.PID
        ]

    def inspect(self, port: str) -> dict:
        matches = [item for item in self.ports() if item["port"] == port]
        if len(matches) != 1:
            raise UsbResetError("exactly one supported board on the explicit port is required")
        with self.client_factory(port, timeout=3.0) as client:
            state = client.request("read_node_state")["result"]
        hardware_id = state.get("hardware_id")
        if not isinstance(hardware_id, str) or not HARDWARE_ID_PATTERN.fullmatch(hardware_id):
            raise UsbResetError("sensor did not report a valid immutable hardware identity")
        return {**matches[0], **state}

    def prepare(self, record_id: int, device_id: str, hardware_id: str, port: str) -> tuple[str, dict]:
        state = self.inspect(port)
        if state.get("hardware_id") != hardware_id or state.get("node_id") != device_id:
            raise UsbResetError("connected physical sensor does not match the selected gateway sensor")
        token = secrets.token_urlsafe(32)
        with self._confirmation_lock:
            self._confirmations[token] = (
                record_id, device_id, hardware_id, "application_factory", port, monotonic() + 120
            )
        return token, state

    def cancel(self, token: str, record_id: int, device_id: str, hardware_id: str, port: str) -> None:
        with self._confirmation_lock:
            pending = self._confirmations.pop(token, None)
        if pending is None or pending[:5] != (record_id, device_id, hardware_id, "application_factory", port):
            raise UsbResetError("factory-reset confirmation is missing, expired, reused, or mismatched")

    def authorize(self, token: str, record_id: int, device_id: str, hardware_id: str, port: str) -> None:
        with self._confirmation_lock:
            pending = self._confirmations.pop(token, None)
        expected = (record_id, device_id, hardware_id, "application_factory", port)
        if pending is None or pending[:5] != expected or pending[5] <= monotonic():
            raise UsbResetError("factory-reset confirmation is missing, expired, reused, or mismatched")

    def launch(self, record_id: int, device_id: str, hardware_id: str, port: str) -> UsbResetOperation:
        lock = self._locks.setdefault(hardware_id, asyncio.Lock())
        if lock.locked():
            raise UsbResetError("a reset operation is already active for this physical sensor")
        operation = UsbResetOperation(record_id, uuid4().hex, device_id, hardware_id, port)
        self.operations[operation.operation_id] = operation
        asyncio.create_task(self._run(operation, lock))
        return operation

    def start(self, token: str, record_id: int, device_id: str, hardware_id: str, port: str) -> UsbResetOperation:
        self.authorize(token, record_id, device_id, hardware_id, port)
        return self.launch(record_id, device_id, hardware_id, port)

    async def _run(self, operation: UsbResetOperation, lock: asyncio.Lock) -> None:
        async with lock:
            try:
                operation.state = "preparing"
                before = await asyncio.to_thread(self.inspect, operation.port)
                operation.progress.append("physical_identity_verified")
                with self.client_factory(operation.port, timeout=3.0) as client:
                    prepared = client.request(
                        "prepare_factory_reset", reset_protocol_version=2, scope="application_factory",
                        expected_hardware_id=operation.hardware_id,
                    )["result"]
                    operation.progress.append("device_challenge_received")
                    if prepared.get("hardware_id") != operation.hardware_id:
                        raise UsbResetError("device reset challenge identity mismatch")
                    accepted = client.request(
                        "confirm_factory_reset", reset_protocol_version=2, scope="application_factory",
                        expected_hardware_id=operation.hardware_id, operation_id=prepared["operation_id"],
                        challenge=prepared["challenge"],
                    )["result"]
                if accepted.get("operation_id") != prepared.get("operation_id"):
                    raise UsbResetError("reset operation correlation mismatch")
                operation.state = "rebooting"
                operation.progress.append("pre_reboot_key_deletion_verified")
                deadline = monotonic() + self.reboot_timeout
                verified = None
                while monotonic() < deadline and verified is None:
                    await asyncio.sleep(0.5)
                    for candidate in self.ports():
                        try:
                            state = await asyncio.to_thread(self.inspect, candidate["port"])
                        except (OSError, ValueError, ProtocolError, RuntimeError):
                            continue
                        if state.get("hardware_id") == operation.hardware_id:
                            verified = state
                            break
                if verified is None:
                    raise UsbResetError("same physical sensor did not re-enumerate")
                if verified.get("node_id") is not None or verified.get("identity_status") != "unprovisioned":
                    raise UsbResetError("post-reset sensor is not unprovisioned")
                if not before.get("firmware_version") or verified.get("firmware_version") != before.get("firmware_version"):
                    raise UsbResetError("installed firmware changed during reset")
                operation.post_reset = verified
                operation.physical_reset_complete = True
                operation.state = "physical_complete"
                operation.progress.extend(["same_hardware_reenumerated", "post_reset_unprovisioned_verified"])
            except Exception as exc:
                operation.state = "failed"
                operation.error = str(exc)[:500]

import asyncio
import re
from collections.abc import Callable
from contextlib import asynccontextmanager

from gateway.app.ble.connection import DeviceConnection
from gateway.app.config import Settings

LED_COMMAND = re.compile(r"LED (?:ON|OFF|(?:[0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5]))$")
RATE_COMMAND = re.compile(r"RATE (?:[5-9][0-9]|[1-9][0-9]{2}|[1-4][0-9]{3}|5000)$")
MODE_COMMAND = re.compile(r"MODE (?:LIVE|EDGE_SUMMARY|LOW_POWER)$")


def validate_command(command: str) -> str:
    command = " ".join(command.strip().upper().split())
    if command == "PING" or LED_COMMAND.fullmatch(command) or RATE_COMMAND.fullmatch(command) or MODE_COMMAND.fullmatch(command):
        return command
    raise ValueError("command is not in the supported allowlist")


class BleManager:
    def __init__(self, settings: Settings, telemetry_callback, status_callback, client_factory: Callable | None = None) -> None:
        self.settings = settings
        self.telemetry_callback = telemetry_callback
        self.status_callback = status_callback
        self.client_factory = client_factory
        self.connections: dict[str, DeviceConnection] = {}
        self.reporting_modes: dict[str, str] = {}
        self._identify_locks: dict[str, asyncio.Lock] = {}
        self.semaphore = asyncio.Semaphore(settings.max_connection_attempts)
        self._pause_lock = asyncio.Lock()

    def schedule(self, device_id: str, address: str) -> DeviceConnection:
        self.reporting_modes.setdefault(device_id, "EDGE_SUMMARY")
        existing = self.connections.get(device_id)
        if existing is not None:
            if existing.address != address:
                existing.address = address
            existing.start()
            return existing

        async def status(state: str, error: str | None) -> None:
            await self.status_callback(device_id, state, error)

        connection = DeviceConnection(
            device_id,
            address,
            telemetry_callback=self.telemetry_callback,
            status_callback=status,
            connection_semaphore=self.semaphore,
            client_factory=self.client_factory,
            poll_interval=self.settings.poll_interval_seconds,
            initial_backoff=self.settings.reconnect_initial_seconds,
            max_backoff=self.settings.reconnect_max_seconds,
            stable_seconds=self.settings.reconnect_stable_seconds,
        )
        self.connections[device_id] = connection
        connection.start()
        return connection

    async def disconnect(self, device_id: str) -> None:
        connection = self.connections.get(device_id)
        if connection:
            await connection.disconnect()

    async def remove(self, device_id: str) -> None:
        connection = self.connections.pop(device_id, None)
        if connection:
            await connection.stop()

    @asynccontextmanager
    async def paused_connections(self):
        """Temporarily stop managed reconnect loops during exclusive BLE work."""
        async with self._pause_lock:
            scheduled = [(device_id, connection.address) for device_id, connection in self.connections.items()]
            for device_id, _address in scheduled:
                await self.remove(device_id)
            try:
                yield
            finally:
                for device_id, address in scheduled:
                    self.schedule(device_id, address)

    async def command(self, device_id: str, command: str) -> str:
        normalized = validate_command(command)
        connection = self.connections.get(device_id)
        if connection is None:
            raise ConnectionError("device is not managed")
        await connection.send_command(normalized)
        if normalized == "MODE LIVE":
            self.reporting_modes[device_id] = "LIVE"
        elif normalized == "MODE EDGE_SUMMARY":
            self.reporting_modes[device_id] = "EDGE_SUMMARY"
        elif normalized == "MODE LOW_POWER":
            self.reporting_modes[device_id] = "LOW_POWER"
        return normalized

    async def identify(self, device_id: str) -> None:
        """Run a bounded, distinctive LED pattern and leave the indicator off."""
        lock = self._identify_locks.setdefault(device_id, asyncio.Lock())
        if lock.locked():
            raise ConnectionError("device identification is already in progress")
        async with lock:
            pattern = [
                ("LED OFF", 0.15),
                ("LED ON", 0.18), ("LED OFF", 0.15),
                ("LED ON", 0.18), ("LED OFF", 0.15),
                ("LED ON", 0.18), ("LED OFF", 0.45),
                ("LED ON", 0.70), ("LED OFF", 0.15),
            ]
            try:
                for command, delay in pattern:
                    await self.command(device_id, command)
                    await asyncio.sleep(delay)
            finally:
                await self.command(device_id, "LED OFF")

    async def persistence_acknowledgement(self, device_id: str, boot_id: str, sequence: int) -> None:
        connection = self.connections.get(device_id)
        if connection is None:
            return
        await connection.send_persistence_ack(boot_id, sequence)

    def runtime(self, device_id: str) -> dict:
        connection = self.connections.get(device_id)
        return (
            {"connection_status": connection.state, "last_error": connection.last_error,
             "reporting_mode": self.reporting_modes.get(device_id, "EDGE_SUMMARY")}
            if connection
            else {"connection_status": "disconnected", "last_error": None,
                  "reporting_mode": self.reporting_modes.get(device_id, "UNKNOWN")}
        )

    async def shutdown(self) -> None:
        await asyncio.gather(*(connection.stop() for connection in list(self.connections.values())), return_exceptions=True)
        self.connections.clear()

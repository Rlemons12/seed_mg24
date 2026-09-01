import asyncio
import math
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from gateway.app.ble.connection import DeviceConnection
from gateway.app.config import Settings

LED_COMMAND = re.compile(r"LED (?:ON|OFF|(?:[0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5]))$")
RATE_COMMAND = re.compile(r"RATE (?:[5-9][0-9]|[1-9][0-9]{2}|[1-4][0-9]{3}|5000)$")
MODE_COMMAND = re.compile(r"MODE (?:LIVE|LIVE_NEXT_WAKE|LOW_POWER)$")
LOW_POWER_WAKE_INTERVAL_SECONDS = 300


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
        self.low_power_started_at: dict[str, datetime] = {}
        self.live_on_next_wake: set[str] = set()
        self.low_power_on_next_wake: set[str] = set()
        self.live_mode_ends_at: dict[str, datetime] = {}
        self._live_mode_tasks: dict[str, asyncio.Task] = {}
        self._pending_live_locks: dict[str, asyncio.Lock] = {}
        self._identify_locks: dict[str, asyncio.Lock] = {}
        self.semaphore = asyncio.Semaphore(settings.max_connection_attempts)
        self._pause_lock = asyncio.Lock()

    def schedule(self, device_id: str, address: str) -> DeviceConnection:
        self.reporting_modes.setdefault(device_id, "LIVE")
        if self.reporting_modes[device_id] == "LIVE" and device_id not in self._live_mode_tasks:
            self._schedule_live_mode_timeout(device_id)
        existing = self.connections.get(device_id)
        if existing is not None:
            if existing.address != address:
                existing.address = address
            existing.start()
            return existing

        async def status(state: str, error: str | None) -> None:
            await self.status_callback(device_id, state, error)

        async def telemetry(data_device_id: str, data: bytes) -> None:
            if self.reporting_modes.get(data_device_id) == "LOW_POWER":
                self.low_power_started_at[data_device_id] = datetime.now(UTC)
            if data_device_id in self.live_on_next_wake:
                await self._apply_live_on_next_wake(data_device_id)
            if data_device_id in self.low_power_on_next_wake:
                await self._apply_low_power_on_next_wake(data_device_id)
            await self.telemetry_callback(data_device_id, data)

        connection = DeviceConnection(
            device_id,
            address,
            telemetry_callback=telemetry,
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
        self._cancel_live_mode_timeout(device_id)
        self.low_power_on_next_wake.discard(device_id)
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
        if normalized == "MODE LIVE_NEXT_WAKE":
            if device_id not in self.connections:
                raise ConnectionError("device is not managed")
            self.live_on_next_wake.add(device_id)
            return normalized
        connection = self.connections.get(device_id)
        if connection is None:
            raise ConnectionError("device is not managed")
        await connection.send_command(normalized)
        if normalized == "MODE LIVE":
            self.reporting_modes[device_id] = "LIVE"
            self.live_on_next_wake.discard(device_id)
            self.low_power_on_next_wake.discard(device_id)
            self.low_power_started_at.pop(device_id, None)
            self._schedule_live_mode_timeout(device_id)
        elif normalized == "MODE LOW_POWER":
            self.reporting_modes[device_id] = "LOW_POWER"
            self.live_on_next_wake.discard(device_id)
            self.low_power_on_next_wake.discard(device_id)
            self.low_power_started_at[device_id] = datetime.now(UTC)
            self._cancel_live_mode_timeout(device_id)
        return normalized

    async def _apply_live_on_next_wake(self, device_id: str) -> None:
        lock = self._pending_live_locks.setdefault(device_id, asyncio.Lock())
        if lock.locked() or device_id not in self.live_on_next_wake:
            return
        async with lock:
            if device_id not in self.live_on_next_wake:
                return
            connection = self.connections.get(device_id)
            if connection is None or connection.state != "connected":
                return
            try:
                await connection.send_command("MODE LIVE")
            except (ConnectionError, TimeoutError):
                return
            self.reporting_modes[device_id] = "LIVE"
            self.live_on_next_wake.discard(device_id)
            self.low_power_started_at.pop(device_id, None)
            self._schedule_live_mode_timeout(device_id)

    def _cancel_live_mode_timeout(self, device_id: str) -> None:
        self.live_mode_ends_at.pop(device_id, None)
        task = self._live_mode_tasks.pop(device_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _schedule_live_mode_timeout(self, device_id: str) -> None:
        self._cancel_live_mode_timeout(device_id)
        self.live_mode_ends_at[device_id] = datetime.now(UTC) + timedelta(seconds=self.settings.live_mode_max_seconds)
        self._live_mode_tasks[device_id] = asyncio.create_task(
            self._expire_live_mode(device_id), name=f"live-timeout-{device_id}"
        )

    async def _expire_live_mode(self, device_id: str) -> None:
        await asyncio.sleep(self.settings.live_mode_max_seconds)
        if self.reporting_modes.get(device_id) != "LIVE":
            return
        connection = self.connections.get(device_id)
        if connection is not None and connection.state == "connected":
            try:
                await connection.send_command("MODE LOW_POWER")
            except (ConnectionError, TimeoutError):
                self.low_power_on_next_wake.add(device_id)
                return
            self.reporting_modes[device_id] = "LOW_POWER"
            self.low_power_started_at[device_id] = datetime.now(UTC)
            self.live_mode_ends_at.pop(device_id, None)
            return
        self.low_power_on_next_wake.add(device_id)

    async def _apply_low_power_on_next_wake(self, device_id: str) -> None:
        if device_id not in self.low_power_on_next_wake:
            return
        connection = self.connections.get(device_id)
        if connection is None or connection.state != "connected":
            return
        try:
            await connection.send_command("MODE LOW_POWER")
        except (ConnectionError, TimeoutError):
            return
        self.reporting_modes[device_id] = "LOW_POWER"
        self.low_power_started_at[device_id] = datetime.now(UTC)
        self.low_power_on_next_wake.discard(device_id)
        self._cancel_live_mode_timeout(device_id)

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

    def runtime(self, device_id: str, last_telemetry_at: datetime | None = None) -> dict:
        connection = self.connections.get(device_id)
        mode = self.reporting_modes.get(device_id, "LIVE" if connection else "UNKNOWN")
        next_wake_at = None
        seconds_to_next_wake = None
        if mode == "LOW_POWER":
            now = datetime.now(UTC)
            anchors = [self.low_power_started_at.get(device_id), last_telemetry_at]
            anchors = [item.replace(tzinfo=UTC) if item and item.tzinfo is None else item for item in anchors if item]
            if anchors:
                anchor = max(anchors)
                next_wake_at = anchor + timedelta(seconds=LOW_POWER_WAKE_INTERVAL_SECONDS)
                seconds_to_next_wake = max(0, math.ceil((next_wake_at - now).total_seconds()))
        low_power = {
            "low_power_wake_interval_seconds": LOW_POWER_WAKE_INTERVAL_SECONDS,
            "low_power_next_wake_at": next_wake_at,
            "low_power_seconds_to_next_wake": seconds_to_next_wake,
            "live_on_next_wake": device_id in self.live_on_next_wake,
            "live_mode_ends_at": self.live_mode_ends_at.get(device_id),
            "low_power_on_next_wake": device_id in self.low_power_on_next_wake,
        }
        return (
            {"connection_status": connection.state, "last_error": connection.last_error,
             "reporting_mode": mode, **low_power}
            if connection
            else {"connection_status": "disconnected", "last_error": None,
                  "reporting_mode": mode, **low_power}
        )

    async def shutdown(self) -> None:
        for task in self._live_mode_tasks.values():
            task.cancel()
        if self._live_mode_tasks:
            await asyncio.gather(*self._live_mode_tasks.values(), return_exceptions=True)
        self._live_mode_tasks.clear()
        await asyncio.gather(*(connection.stop() for connection in list(self.connections.values())), return_exceptions=True)
        self.connections.clear()

import asyncio
import math
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from gateway.app.ble.connection import DeviceConnection
from gateway.app.ble.telemetry_parser import TelemetryParseError, parse_telemetry
from gateway.app.config import Settings
from gateway.app.schemas import NormalizedTelemetry

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
        self.actual_modes: dict[str, str] = {}
        # Compatibility alias for internal callers/tests; values are sensor-confirmed only.
        self.reporting_modes = self.actual_modes
        self.requested_modes: dict[str, str] = {}
        self.transition_states: dict[str, str] = {}
        self.transition_acknowledged: dict[str, bool] = {}
        self.low_power_started_at: dict[str, datetime] = {}
        self.live_on_next_wake: set[str] = set()
        self.live_mode_ends_at: dict[str, datetime] = {}
        self._live_mode_tasks: dict[str, asyncio.Task] = {}
        self._pending_live_locks: dict[str, asyncio.Lock] = {}
        self._identify_locks: dict[str, asyncio.Lock] = {}
        self.semaphore = asyncio.Semaphore(settings.max_connection_attempts)
        self._pause_lock = asyncio.Lock()

    def schedule(self, device_id: str, address: str) -> DeviceConnection:
        self.actual_modes.setdefault(device_id, "UNKNOWN")
        self.transition_states.setdefault(device_id, "UNKNOWN")
        existing = self.connections.get(device_id)
        if existing is not None:
            if existing.address != address:
                existing.address = address
            existing.start()
            return existing

        async def status(state: str, error: str | None) -> None:
            if state != "connected":
                self.actual_modes[device_id] = "UNKNOWN"
            await self.status_callback(device_id, state, error)

        async def telemetry(data_device_id: str, data: bytes) -> None:
            evidence = self._observe_runtime_evidence(data_device_id, data)
            is_current_low_power_wake = (
                evidence is not None
                and evidence.runtime_mode == "LOW_POWER"
                and evidence.record_type in {"measurement", "heartbeat"}
                and not evidence.delayed
            )
            if data_device_id in self.live_on_next_wake and is_current_low_power_wake:
                await self._apply_live_on_next_wake(data_device_id)
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

    async def remove(self, device_id: str, *, preserve_runtime_intent: bool = False) -> None:
        """Stop managing a connection; explicit stops clear runtime intent by default.

        Exclusive configuration/provisioning callers may preserve intent while the
        transport is paused. Actual mode still becomes unknown while disconnected.
        """
        self._cancel_live_mode_timeout(device_id)
        connection = self.connections.pop(device_id, None)
        if connection:
            await connection.stop()
        self.actual_modes[device_id] = "UNKNOWN"
        if not preserve_runtime_intent:
            self.live_on_next_wake.discard(device_id)
            self.requested_modes.pop(device_id, None)
            self.transition_acknowledged.pop(device_id, None)
            self.transition_states[device_id] = "UNKNOWN"
            self.low_power_started_at.pop(device_id, None)

    @asynccontextmanager
    async def paused_connections(self):
        """Temporarily stop managed reconnect loops during exclusive BLE work."""
        async with self._pause_lock:
            scheduled = [(device_id, connection.address) for device_id, connection in self.connections.items()]
            for device_id, _address in scheduled:
                await self.remove(device_id, preserve_runtime_intent=True)
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
            self.requested_modes[device_id] = "LIVE"
            self.transition_states[device_id] = "LIVE_REQUESTED"
            self.transition_acknowledged[device_id] = False
            return normalized
        connection = self.connections.get(device_id)
        if connection is None:
            raise ConnectionError("device is not managed")
        target = "LIVE" if normalized == "MODE LIVE" else "LOW_POWER" if normalized == "MODE LOW_POWER" else None
        if target:
            self.requested_modes[device_id] = target
            self.transition_states[device_id] = f"{target}_REQUESTED"
            self.transition_acknowledged[device_id] = False
        try:
            await connection.send_command(normalized)
        except (ConnectionError, TimeoutError):
            if target:
                self.transition_states[device_id] = f"{target}_FAILED"
            raise
        if target:
            self.transition_states[device_id] = f"{target}_PENDING"
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
            self.requested_modes[device_id] = "LIVE"
            self.transition_states[device_id] = "LIVE_PENDING"
            self.transition_acknowledged[device_id] = False

    def _observe_runtime_evidence(self, device_id: str, data: bytes) -> NormalizedTelemetry | None:
        try:
            payload = parse_telemetry(data, max_payload_bytes=self.settings.max_payload_bytes)
        except TelemetryParseError:
            return None
        code = payload.metadata.get("code")
        if payload.record_type == "config_ack" and code in {"mode_live", "mode_low_power"}:
            target = "LIVE" if code == "mode_live" else "LOW_POWER"
            if self.requested_modes.get(device_id) == target:
                self.transition_states[device_id] = f"{target}_PENDING"
                self.transition_acknowledged[device_id] = True
        # Command acknowledgements prove execution/acceptance. Current measurement or
        # heartbeat telemetry is the stronger sustained-state evidence; replayed rows
        # deliberately omit rm and therefore cannot overwrite current physical state.
        if payload.runtime_mode and payload.record_type in {"measurement", "heartbeat"} and not payload.delayed:
            self._confirm_runtime_mode(device_id, payload.runtime_mode, payload.received_at)
        return payload

    def _confirm_runtime_mode(self, device_id: str, mode: str, observed_at: datetime) -> None:
        previous_mode = self.actual_modes.get(device_id, "UNKNOWN")
        self.actual_modes[device_id] = mode
        requested = self.requested_modes.get(device_id)
        explicit_mode_confirmation = requested == mode
        if requested == mode:
            self.requested_modes.pop(device_id, None)
            self.transition_states[device_id] = f"{mode}_CONFIRMED"
            self.transition_acknowledged.pop(device_id, None)
        elif requested is None:
            self.transition_states[device_id] = f"{mode}_CONFIRMED"
            self.transition_acknowledged.pop(device_id, None)
        if mode == "LIVE":
            self.live_on_next_wake.discard(device_id)
            self.low_power_started_at.pop(device_id, None)
            if explicit_mode_confirmation or previous_mode != "LIVE" or device_id not in self._live_mode_tasks:
                self._schedule_live_mode_timeout(device_id)
        else:
            self.low_power_started_at[device_id] = observed_at
            self._cancel_live_mode_timeout(device_id)

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
        if self.actual_modes.get(device_id) != "LIVE":
            return
        # Firmware owns the physical timeout. The gateway records an expectation and
        # waits for sensor evidence instead of racing it with a second mode command.
        self.requested_modes[device_id] = "LOW_POWER"
        self.transition_states[device_id] = "LOW_POWER_PENDING"
        self.transition_acknowledged[device_id] = False
        self.live_mode_ends_at.pop(device_id, None)

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
        mode = self.actual_modes.get(device_id, "UNKNOWN")
        next_wake_at = None
        seconds_to_next_wake = None
        if mode == "LOW_POWER":
            now = datetime.now(UTC)
            anchor = self.low_power_started_at.get(device_id)
            if anchor:
                if anchor.tzinfo is None:
                    anchor = anchor.replace(tzinfo=UTC)
                next_wake_at = anchor + timedelta(seconds=LOW_POWER_WAKE_INTERVAL_SECONDS)
                seconds_to_next_wake = max(0, math.ceil((next_wake_at - now).total_seconds()))
        low_power = {
            "low_power_wake_interval_seconds": LOW_POWER_WAKE_INTERVAL_SECONDS,
            "low_power_next_wake_at": next_wake_at,
            "low_power_seconds_to_next_wake": seconds_to_next_wake,
            "live_on_next_wake": device_id in self.live_on_next_wake,
            "live_mode_ends_at": self.live_mode_ends_at.get(device_id),
            "low_power_on_next_wake": False,
            "requested_mode": self.requested_modes.get(device_id),
            "actual_mode": mode,
            "transition_state": self.transition_states.get(device_id, "UNKNOWN"),
            "transition_acknowledged": self.transition_acknowledged.get(device_id, False),
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

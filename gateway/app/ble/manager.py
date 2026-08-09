import asyncio
import re
from collections.abc import Callable

from gateway.app.ble.connection import DeviceConnection
from gateway.app.config import Settings

LED_COMMAND = re.compile(r"LED (?:ON|OFF|(?:[0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5]))$")
RATE_COMMAND = re.compile(r"RATE (?:[5-9][0-9]|[1-9][0-9]{2}|[1-4][0-9]{3}|5000)$")


def validate_command(command: str) -> str:
    command = " ".join(command.strip().upper().split())
    if command == "PING" or LED_COMMAND.fullmatch(command) or RATE_COMMAND.fullmatch(command):
        return command
    raise ValueError("command is not in the supported allowlist")


class BleManager:
    def __init__(self, settings: Settings, telemetry_callback, status_callback, client_factory: Callable | None = None) -> None:
        self.settings = settings
        self.telemetry_callback = telemetry_callback
        self.status_callback = status_callback
        self.client_factory = client_factory
        self.connections: dict[str, DeviceConnection] = {}
        self.semaphore = asyncio.Semaphore(settings.max_connection_attempts)

    def schedule(self, device_id: str, address: str) -> DeviceConnection:
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

    async def command(self, device_id: str, command: str) -> str:
        normalized = validate_command(command)
        connection = self.connections.get(device_id)
        if connection is None:
            raise ConnectionError("device is not managed")
        await connection.send_command(normalized)
        return normalized

    def runtime(self, device_id: str) -> dict:
        connection = self.connections.get(device_id)
        return (
            {"connection_status": connection.state, "last_error": connection.last_error}
            if connection
            else {"connection_status": "disconnected", "last_error": None}
        )

    async def shutdown(self) -> None:
        await asyncio.gather(*(connection.stop() for connection in list(self.connections.values())), return_exceptions=True)
        self.connections.clear()

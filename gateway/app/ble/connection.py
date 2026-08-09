import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from gateway.app.ble.constants import CAPABILITIES_UUID, COMMAND_UUID, METADATA_UUID, TELEMETRY_UUID
from gateway.app.ble.telemetry_parser import TelemetryParseError

logger = logging.getLogger(__name__)


StatusCallback = Callable[[str, str | None], Awaitable[None]]
TelemetryCallback = Callable[[str, bytes], Awaitable[None]]


def reconnect_delay(initial: float, maximum: float, failure_count: int) -> float:
    return min(initial * (2 ** max(0, failure_count)), maximum)


class DeviceConnection:
    def __init__(
        self,
        device_id: str,
        address: str,
        *,
        telemetry_callback: TelemetryCallback,
        status_callback: StatusCallback,
        connection_semaphore: asyncio.Semaphore,
        client_factory: Callable[..., Any] | None = None,
        poll_interval: float = 1.0,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
        stable_seconds: float = 30.0,
        random_source: random.Random | None = None,
    ) -> None:
        self.device_id = device_id
        self.address = address
        self.telemetry_callback = telemetry_callback
        self.status_callback = status_callback
        self.connection_semaphore = connection_semaphore
        self.client_factory = client_factory
        self.poll_interval = poll_interval
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.stable_seconds = stable_seconds
        self.random = random_source or random.Random()
        self.state = "disconnected"
        self.last_error: str | None = None
        self.metadata: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._commands: asyncio.Queue[tuple[str, asyncio.Future]] = asyncio.Queue(maxsize=32)
        self._stop = asyncio.Event()
        self._force_disconnect = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._client = None
        self._notification_active = False
        self._connected_since: float | None = None

    async def _set_state(self, state: str, error: str | None = None) -> None:
        self.state = state
        self.last_error = error
        await self.status_callback(state, error)

    def start(self) -> None:
        if not self._task or self._task.done():
            self._stop.clear()
            self._force_disconnect.clear()
            self._task = asyncio.create_task(self.run(), name=f"ble-{self.device_id}")

    async def stop(self) -> None:
        self._stop.set()
        self._force_disconnect.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self._set_state("disconnected")

    async def disconnect(self) -> None:
        self._force_disconnect.set()
        client = self._client
        if client and getattr(client, "is_connected", False):
            await client.disconnect()

    async def send_command(self, command: str, timeout: float = 5.0) -> None:
        if self.state != "connected":
            raise ConnectionError("device is not connected")
        future = asyncio.get_running_loop().create_future()
        self._commands.put_nowait((command, future))
        await asyncio.wait_for(future, timeout=timeout)

    async def run(self) -> None:
        failure_count = 0
        while not self._stop.is_set():
            connected_duration = 0.0
            try:
                self._force_disconnect.clear()
                await self._set_state("connecting")
                connected_duration = await self._connected_loop()
                if self._stop.is_set() or self._force_disconnect.is_set():
                    break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if self._connected_since is not None:
                    connected_duration = monotonic() - self._connected_since
                safe_error = type(exc).__name__
                logger.warning("BLE device %s connection failed: %s", self.device_id, safe_error)
                await self._set_state("error", safe_error)
            self._connected_since = None
            if connected_duration >= self.stable_seconds:
                failure_count = 0
            delay = reconnect_delay(self.initial_backoff, self.max_backoff, failure_count)
            delay += self.random.uniform(0, delay * 0.2)
            await self._set_state("backoff", self.last_error)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            failure_count += 1

    async def _connected_loop(self) -> float:
        if self.client_factory is None:
            from bleak import BleakClient

            factory = BleakClient
        else:
            factory = self.client_factory
        disconnected = asyncio.Event()

        def on_disconnect(_client) -> None:
            disconnected.set()

        async with self._lock:
            client = factory(self.address, disconnected_callback=on_disconnect)
            try:
                async with self.connection_semaphore:
                    await client.connect()
                self._client = client
                connected_at = monotonic()
                self._connected_since = connected_at
                await self._read_metadata(client)
                await self._read_capabilities(client)
                self._notification_active = False

                def notify(_sender, data: bytearray) -> None:
                    asyncio.create_task(self._safe_telemetry(bytes(data)))

                try:
                    await client.start_notify(TELEMETRY_UUID, notify)
                    self._notification_active = True
                except Exception:
                    logger.info("Notifications unavailable for %s; using bounded polling", self.device_id)
                await self._set_state("connected")
                while not self._stop.is_set() and not self._force_disconnect.is_set() and not disconnected.is_set():
                    await self._drain_commands(client)
                    if not self._notification_active:
                        try:
                            data = await client.read_gatt_char(TELEMETRY_UUID)
                            await self._safe_telemetry(bytes(data))
                        except Exception as exc:
                            logger.debug("Polling %s failed: %s", self.device_id, type(exc).__name__)
                    try:
                        await asyncio.wait_for(disconnected.wait(), timeout=self.poll_interval)
                    except TimeoutError:
                        pass
            finally:
                self._client = None
                self._notification_active = False
                if getattr(client, "is_connected", False):
                    await client.disconnect()
        if not self._stop.is_set() and not self._force_disconnect.is_set():
            raise ConnectionError("BLE connection closed")
        return monotonic() - connected_at

    async def _safe_telemetry(self, data: bytes) -> None:
        try:
            await self.telemetry_callback(self.device_id, data)
        except (TelemetryParseError, ValueError) as exc:
            logger.warning("Rejected telemetry from %s: %s", self.device_id, str(exc)[:160])
        except Exception:
            logger.exception("Telemetry handling failed for %s", self.device_id)

    async def _read_metadata(self, client) -> None:
        try:
            raw = bytes(await client.read_gatt_char(METADATA_UUID))
            if len(raw) > 512:
                raise ValueError("metadata too large")
            metadata = json.loads(raw.decode("utf-8").strip("\x00"))
            if not isinstance(metadata, dict):
                raise ValueError("metadata is not an object")
            # Legacy metadata remains readable for diagnostics, but is not considered fully compatible.
            identity = metadata.get("node_id", metadata.get("id"))
            # Windows may cache the pre-commissioning dynamic value. The address-to-node mapping
            # is created only after a verified PROVGET readback, so tolerate only that sentinel.
            if identity not in (None, self.device_id, "UNASSIGNED-MG24"):
                raise ValueError("stable firmware identity mismatch")
            if identity == "UNASSIGNED-MG24":
                metadata["node_id"] = self.device_id
            self.metadata = {key: metadata[key] for key in (
                "node_id", "sensor_package_version", "firmware_version", "protocol_version",
                "configuration_schema_version", "build_identifier", "git_commit", "id", "fw", "v", "dt"
            ) if key in metadata}
        except ValueError:
            raise
        except Exception:
            self.metadata = {}

    async def _read_capabilities(self, client) -> None:
        try:
            raw = bytes(await client.read_gatt_char(CAPABILITIES_UUID))
            if len(raw) > 2048:
                raise ValueError("capability response too large")
            capabilities = json.loads(raw.decode("utf-8").strip("\x00"))
            if not isinstance(capabilities, dict) or capabilities.get("schema_version") != 1:
                raise ValueError("unsupported capability response")
            if capabilities.get("node_id") not in {self.device_id, "UNASSIGNED-MG24"}:
                raise ValueError("capability node identity mismatch")
            if capabilities.get("node_id") == "UNASSIGNED-MG24":
                capabilities["node_id"] = self.device_id
            self.capabilities = capabilities
        except ValueError:
            raise
        except Exception:
            self.capabilities = {}

    async def _drain_commands(self, client) -> None:
        while not self._commands.empty():
            command, future = await self._commands.get()
            try:
                await client.write_gatt_char(COMMAND_UUID, (command + "\n").encode("ascii"), response=True)
                if not future.done():
                    future.set_result(None)
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
                raise

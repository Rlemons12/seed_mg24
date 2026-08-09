import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from gateway.app.ble.constants import LEGACY_NAME, SERVICE_UUID
from gateway.app.schemas import Discovery


class BleScannerService:
    def __init__(self, scan_duration: float, ttl_seconds: float, scanner_factory: Callable[..., Any] | None = None) -> None:
        self.scan_duration = scan_duration
        self.ttl_seconds = ttl_seconds
        self.scanner_factory = scanner_factory
        self._discoveries: dict[str, tuple[Discovery, float]] = {}
        self._scan_task: asyncio.Task | None = None

    @staticmethod
    def classify(name: str | None, service_uuids: list[str]) -> tuple[bool, str]:
        normalized = {uuid.lower() for uuid in service_uuids}
        if SERVICE_UUID in normalized:
            return True, "advertises the Seed MG24 telemetry service"
        if name == LEGACY_NAME:
            return False, "legacy name matches but service compatibility is not confirmed"
        return False, "telemetry service is not advertised"

    async def start_scan(self) -> bool:
        if self._scan_task and not self._scan_task.done():
            return False
        self._scan_task = asyncio.create_task(self._scan(), name="ble-discovery-scan")
        return True

    async def wait_for_scan(self) -> None:
        if self._scan_task:
            await self._scan_task

    async def _scan(self) -> None:
        if self.scanner_factory is None:
            from bleak import BleakScanner

            scanner_factory = BleakScanner
        else:
            scanner_factory = self.scanner_factory

        def detection_callback(device, advertisement) -> None:
            services = list(advertisement.service_uuids or [])[:32]
            name = advertisement.local_name or getattr(device, "name", None)
            compatible, reason = self.classify(name, services)
            rssi = getattr(advertisement, "rssi", None)
            discovery = Discovery(
                address=device.address,
                name=name,
                rssi=rssi,
                service_uuids=services,
                compatible=compatible,
                compatibility_reason=reason,
                temporary_id=f"unassigned:{device.address.lower()}",
                last_seen_at=datetime.now(UTC),
            )
            self._discoveries[device.address] = (discovery, monotonic())

        scanner = scanner_factory(detection_callback=detection_callback)
        async with scanner:
            await asyncio.sleep(self.scan_duration)
        self._expire()

    def _expire(self) -> None:
        cutoff = monotonic() - self.ttl_seconds
        for address, (_, observed) in list(self._discoveries.items()):
            if observed < cutoff:
                del self._discoveries[address]

    def discoveries(self) -> list[Discovery]:
        self._expire()
        return sorted(
            (value[0] for value in self._discoveries.values()),
            key=lambda item: (not item.compatible, item.name or "", item.address),
        )

    def get(self, address: str) -> Discovery | None:
        self._expire()
        item = self._discoveries.get(address)
        return item[0] if item else None

    def record(self, discovery: Discovery) -> None:
        """Add a service-inspected or test discovery to the cache."""
        self._discoveries[discovery.address] = (discovery, monotonic())

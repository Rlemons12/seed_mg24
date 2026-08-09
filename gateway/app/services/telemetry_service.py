import hashlib
import json
from collections import OrderedDict
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from gateway.app.ble.telemetry_parser import parse_telemetry
from gateway.app.models import Reading, utc_now
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.repositories.installation_repository import InstallationRepository
from gateway.app.repositories.reading_repository import ReadingRepository
from gateway.app.services.node_capability_service import NodeCapabilityService
from gateway.app.services.websocket_manager import WebSocketManager


class TelemetryService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        websocket_manager: WebSocketManager,
        max_payload_bytes: int = 2048,
        max_payload_json_bytes: int = 4096,
    ) -> None:
        self.session_factory = session_factory
        self.websocket_manager = websocket_manager
        self.max_payload_bytes = max_payload_bytes
        self.max_payload_json_bytes = max_payload_json_bytes
        self._sessions: dict[str, tuple[str, int | None]] = {}
        self._dedupe: dict[str, OrderedDict[str, None]] = {}

    def _session_for(self, device_id: str, uptime: int | None) -> str:
        current = self._sessions.get(device_id)
        if current is None or uptime is not None and current[1] is not None and uptime < current[1]:
            current = (uuid4().hex, uptime)
        elif uptime is not None:
            current = (current[0], uptime)
        self._sessions[device_id] = current
        return current[0]

    def _is_duplicate(self, device_id: str, payload, payload_bytes: bytes) -> bool:
        if payload.sequence_number is not None:
            key = f"s:{payload.sequence_number}"
        else:
            digest = hashlib.blake2s(payload_bytes, digest_size=8).hexdigest()
            key = f"u:{payload.device_uptime_ms}:{digest}"
        cache = self._dedupe.setdefault(device_id, OrderedDict())
        if key in cache:
            return True
        cache[key] = None
        while len(cache) > 256:
            cache.popitem(last=False)
        return False

    async def ingest(self, registered_device_id: str, data: bytes | bytearray | str) -> list[Reading]:
        payload_bytes = data.encode() if isinstance(data, str) else bytes(data)
        payload = parse_telemetry(payload_bytes, max_payload_bytes=self.max_payload_bytes)
        if payload.device_id is not None and payload.device_id != registered_device_id:
            raise ValueError("telemetry device_id does not match the registered device")
        if self._is_duplicate(registered_device_id, payload, payload_bytes):
            return []
        encoded_original = json.dumps(payload.original_payload, separators=(",", ":"), allow_nan=False)
        if len(encoded_original.encode()) > self.max_payload_json_bytes:
            raise ValueError("validated diagnostic payload exceeds storage maximum")
        with self.session_factory() as session:
            devices = DeviceRepository(session)
            device = devices.get(registered_device_id)
            if device is None:
                raise ValueError("registered device no longer exists")
            session_id = self._session_for(registered_device_id, payload.device_uptime_ms)
            capabilities = NodeCapabilityService(devices).get(registered_device_id)
            interface_channels = {item.interface_id: set(item.telemetry_channels) for item in capabilities.interfaces}
            active_installations = [
                item for item in InstallationRepository(session).list() if item.node_id == registered_device_id and item.enabled
            ]
            channel_installations = {
                channel: installation.installation_id
                for installation in active_installations
                for channel in interface_channels.get(installation.interface_id, set())
            }
            channels = payload.channels or {payload.event or payload.record_type: None}
            rows: list[Reading] = []
            for name, channel in channels.items():
                value = channel.value if channel is not None else None
                numeric = float(value) if isinstance(value, (int, float, bool)) else None
                raw = channel.raw_value if channel is not None else None
                rows.append(
                    Reading(
                        registered_device_id=device.id,
                        received_at=payload.received_at,
                        measured_at_device_uptime=payload.device_uptime_ms,
                        device_uptime_ms=payload.device_uptime_ms,
                        sequence_number=payload.sequence_number,
                        session_id=session_id,
                        record_type=payload.record_type,
                        channel=name,
                        raw_value=float(raw) if raw is not None else None,
                        normalized_value=numeric,
                        unit=channel.unit if channel else None,
                        quality=channel.quality if channel else "good",
                        payload_json=encoded_original,
                        delayed=payload.delayed,
                        installation_id=channel_installations.get(name),
                    )
                )
            ReadingRepository(session).add_many(rows)
            installation_repository = InstallationRepository(session)
            for installation in active_installations:
                relevant = [row for row in rows if row.installation_id == installation.installation_id]
                if relevant:
                    valid = any(row.quality not in {"invalid", "sensor_fault", "stale"} for row in relevant)
                    installation_repository.update(
                        installation,
                        last_seen_at=payload.received_at,
                        **({"last_valid_reading_at": payload.received_at} if valid else {}),
                    )
            devices.update_runtime(device, status="connected", last_seen_at=utc_now())
        await self.websocket_manager.broadcast(
            "telemetry",
            registered_device_id,
            {
                "schema_version": payload.schema_version,
                "record_type": payload.record_type,
                "device_uptime_ms": payload.device_uptime_ms,
                "sequence_number": payload.sequence_number,
                "delayed": payload.delayed,
                "event": payload.event,
                "channels": {name: value.model_dump(mode="json") for name, value in payload.channels.items()},
            },
        )
        return rows

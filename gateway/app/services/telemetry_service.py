import asyncio
import hashlib
import json
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from gateway.app.ble.telemetry_parser import parse_telemetry
from gateway.app.models import Reading
from gateway.app.services.telemetry_persistence import TelemetryPersistenceError, TelemetryPersistenceService
from gateway.app.services.vibration_condition import VibrationConditionService
from gateway.app.services.websocket_manager import WebSocketManager

logger = logging.getLogger(__name__)


class TelemetryService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        websocket_manager: WebSocketManager,
        max_payload_bytes: int = 2048,
        max_payload_json_bytes: int = 4096,
        gateway_id: str = "00000000-0000-0000-0000-000000000000",
        persistence_service: TelemetryPersistenceService | None = None,
        vibration_service: VibrationConditionService | None = None,
        acknowledgement_sender: Callable[[str, str, int], Awaitable[None]] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.websocket_manager = websocket_manager
        self.max_payload_bytes = max_payload_bytes
        self.max_payload_json_bytes = max_payload_json_bytes
        self.persistence = persistence_service or TelemetryPersistenceService(session_factory, gateway_id)
        self.vibration = vibration_service or VibrationConditionService(session_factory, gateway_id)
        self.acknowledgement_sender = acknowledgement_sender
        self._sessions: dict[str, tuple[str, int | None, datetime | None]] = {}
        self._dedupe: dict[str, OrderedDict[str, None]] = {}
        self.vibration_counters = {
            "received": 0, "duplicates": 0, "rejected": 0,
            "baseline_eligible": 0, "baseline_excluded": 0, "database_writes": 0,
        }

    def _session_for(self, device_id: str, uptime: int | None, received_at, sensor_boot_id: str | None = None):
        if sensor_boot_id is not None:
            session_id = f"sensor:{sensor_boot_id}"
            current = self._sessions.get(device_id)
            boot_time = received_at - timedelta(milliseconds=uptime) if uptime is not None else None
            if current is None or current[0] != session_id:
                current = (session_id, uptime, boot_time)
            elif uptime is not None:
                current = (session_id, uptime, current[2])
            self._sessions[device_id] = current
            measured_at = current[2] + timedelta(milliseconds=uptime) if uptime is not None and current[2] is not None else None
            return session_id, measured_at
        current = self._sessions.get(device_id)
        if current is None or uptime is not None and current[1] is not None and uptime < current[1]:
            boot_time = received_at - timedelta(milliseconds=uptime) if uptime is not None else None
            current = (uuid4().hex, uptime, boot_time)
        elif uptime is not None:
            current = (current[0], uptime, current[2])
        self._sessions[device_id] = current
        measured_at = current[2] + timedelta(milliseconds=uptime) if uptime is not None and current[2] is not None else None
        return current[0], measured_at

    def _dedupe_key(self, payload, payload_bytes: bytes) -> str:
        if payload.sequence_number is not None:
            return f"{payload.record_type}:s:{payload.sequence_number}"
        digest = hashlib.blake2s(payload_bytes, digest_size=8).hexdigest()
        return f"u:{payload.device_uptime_ms}:{digest}"

    def _is_duplicate(self, device_id: str, key: str) -> bool:
        cache = self._dedupe.setdefault(device_id, OrderedDict())
        return key in cache

    def _remember(self, device_id: str, key: str) -> None:
        cache = self._dedupe.setdefault(device_id, OrderedDict())
        cache[key] = None
        while len(cache) > 256:
            cache.popitem(last=False)

    async def ingest(self, registered_device_id: str, data: bytes | bytearray | str) -> list[Reading]:
        payload_bytes = data.encode() if isinstance(data, str) else bytes(data)
        payload = parse_telemetry(payload_bytes, max_payload_bytes=self.max_payload_bytes)
        if payload.device_id is not None and payload.device_id != registered_device_id:
            raise ValueError("telemetry device_id does not match the registered device")
        dedupe_key = self._dedupe_key(payload, payload_bytes)
        if payload.sensor_boot_id is None and self._is_duplicate(registered_device_id, dedupe_key):
            if payload.record_type == "vibration":
                self.vibration_counters["duplicates"] += 1
            return []
        encoded_original = json.dumps(payload.original_payload, separators=(",", ":"), allow_nan=False)
        if len(encoded_original.encode()) > self.max_payload_json_bytes:
            raise ValueError("validated diagnostic payload exceeds storage maximum")
        session_id, measured_at = self._session_for(
            registered_device_id, payload.device_uptime_ms, payload.received_at, payload.sensor_boot_id
        )
        if payload.record_type == "vibration":
            self.vibration_counters["received"] += 1
            if payload.vibration is None:
                self.vibration_counters["rejected"] += 1
                raise ValueError("vibration message has no validated summary")
            try:
                result = await asyncio.to_thread(
                    self.vibration.process, registered_device_id, payload.vibration,
                    session_id=session_id, observed_at=payload.received_at,
                )
            except Exception:
                self.vibration_counters["rejected"] += 1
                logger.exception(
                    "Vibration window was not processed for node=%s sequence=%s",
                    registered_device_id, payload.vibration.window_sequence,
                )
                raise
            eligibility_key = "baseline_eligible" if payload.vibration.validity == "valid" else "baseline_excluded"
            self.vibration_counters[eligibility_key] += 1
            if result.get("persisted"):
                self.vibration_counters["database_writes"] += 1
            self._remember(registered_device_id, dedupe_key)
            await self.websocket_manager.broadcast("telemetry", registered_device_id, {
                "schema_version": payload.schema_version, "record_type": "vibration",
                "vibration": payload.vibration.model_dump(mode="json"), "condition": result,
            })
            return []
        try:
            outcome = await asyncio.to_thread(
                self.persistence.persist,
                registered_device_id,
                payload,
                session_id=session_id,
                measured_at=measured_at,
                encoded_original=encoded_original,
            )
        except TelemetryPersistenceError:
            logger.exception("Telemetry packet was not persisted for node=%s", registered_device_id)
            raise
        rows = outcome.rows
        self._remember(registered_device_id, dedupe_key)
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
        if (
            self.acknowledgement_sender is not None
            and payload.sensor_boot_id is not None
            and outcome.acknowledged_sequence is not None
        ):
            await self.acknowledgement_sender(
                registered_device_id, payload.sensor_boot_id, outcome.acknowledged_sequence
            )
        return rows

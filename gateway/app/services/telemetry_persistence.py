import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from gateway.app.models import Reading, RegisteredDevice, SensorInstallation, TelemetrySyncState, utc_now
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.schemas import NormalizedTelemetry
from gateway.app.services.node_capability_service import NodeCapabilityService

logger = logging.getLogger(__name__)


class TelemetryPersistenceError(RuntimeError):
    pass


class TelemetryIdentityConflict(TelemetryPersistenceError):
    pass


@dataclass(frozen=True)
class PersistenceOutcome:
    rows: list[Reading]
    acknowledged_sequence: int | None = None
    duplicate: bool = False


class TelemetryPersistenceService:
    """Persist one normalized telemetry packet in one bounded transaction."""

    def __init__(self, session_factory: sessionmaker[Session], gateway_id: str) -> None:
        self.session_factory = session_factory
        self.gateway_id = gateway_id

    def persist(
        self,
        node_id: str,
        payload: NormalizedTelemetry,
        *,
        session_id: str,
        measured_at: datetime | None,
        encoded_original: str,
    ) -> PersistenceOutcome:
        with self.session_factory() as session:
            try:
                device = session.scalar(select(RegisteredDevice).where(RegisteredDevice.device_id == node_id))
                if device is None:
                    raise ValueError("registered device no longer exists")
                if device.archived or not device.enabled or device.lifecycle_state == "removed":
                    raise ValueError("registered device is removed or disabled")

                if payload.sensor_boot_id is not None and payload.sequence_number is not None:
                    existing = list(session.scalars(select(Reading).where(
                        Reading.registered_device_id == device.id,
                        Reading.sensor_boot_id == payload.sensor_boot_id,
                        Reading.sequence_number == payload.sequence_number,
                    )))
                    if existing:
                        expected_channels = set(payload.channels or {payload.event or payload.record_type: None})
                        if {row.channel for row in existing} != expected_channels or any(
                            row.payload_json != encoded_original for row in existing
                        ):
                            state = self._sync_state(session, device.id, payload.sensor_boot_id, payload.sequence_number)
                            state.conflict_count += 1
                            state.updated_at = utc_now()
                            session.commit()
                            raise TelemetryIdentityConflict("telemetry identity was reused with different content")
                        state = self._sync_state(session, device.id, payload.sensor_boot_id, payload.sequence_number)
                        state.duplicate_count += 1
                        state.updated_at = utc_now()
                        session.commit()
                        # The firmware buffer is bounded and may have already dropped a
                        # missing sequence. Acknowledge the record that is durably present;
                        # waiting for a contiguous prefix would permanently block its head.
                        return PersistenceOutcome([], state.highest_seen_sequence, duplicate=True)

                capabilities = NodeCapabilityService(DeviceRepository(session)).get(node_id)
                channel_interfaces = {
                    channel: interface.interface_id for interface in capabilities.interfaces for channel in interface.telemetry_channels
                }
                active_installations = list(
                    session.scalars(
                        select(SensorInstallation).where(
                            SensorInstallation.node_id == node_id,
                            SensorInstallation.enabled.is_(True),
                            SensorInstallation.archived.is_(False),
                        )
                    )
                )
                installation_by_interface = {item.interface_id: item for item in active_installations}
                channels = payload.channels or {payload.event or payload.record_type: None}
                rows: list[Reading] = []
                for name, channel in channels.items():
                    value = channel.value if channel is not None else None
                    numeric = float(value) if isinstance(value, (int, float, bool)) else None
                    raw = channel.raw_value if channel is not None else None
                    interface_id = channel_interfaces.get(name)
                    installation = installation_by_interface.get(interface_id)
                    rows.append(
                        Reading(
                            reading_uuid=str(uuid4()),
                            gateway_id=self.gateway_id,
                            registered_device_id=device.id,
                            received_at=payload.received_at,
                            measured_at=measured_at,
                            measured_at_device_uptime=payload.device_uptime_ms,
                            device_uptime_ms=payload.device_uptime_ms,
                            sequence_number=payload.sequence_number,
                            sensor_boot_id=payload.sensor_boot_id,
                            sample_count=payload.sample_count,
                            session_id=session_id,
                            record_type=payload.record_type,
                            channel=name,
                            raw_value=float(raw) if raw is not None else None,
                            normalized_value=numeric,
                            unit=channel.unit if channel else None,
                            quality=channel.quality if channel else "good",
                            payload_json=encoded_original,
                            delayed=payload.delayed,
                            installation_id=installation.installation_id if installation else None,
                            interface_id=interface_id,
                        )
                    )

                session.add_all(rows)
                for installation in active_installations:
                    relevant = [row for row in rows if row.installation_id == installation.installation_id]
                    if relevant:
                        installation.last_seen_at = payload.received_at
                        if any(row.quality not in {"invalid", "sensor_fault", "stale"} for row in relevant):
                            installation.last_valid_reading_at = payload.received_at
                        installation.updated_at = utc_now()
                device.connection_status = "connected"
                device.last_seen_at = payload.received_at
                device.updated_at = utc_now()
                acknowledged_sequence = None
                if payload.sensor_boot_id is not None and payload.sequence_number is not None:
                    session.flush()
                    state = self._update_sync_state(session, device.id, payload.sensor_boot_id, payload.sequence_number)
                    # Transport acknowledgement follows durable persistence, while the
                    # sync state independently retains gaps for observability.
                    acknowledged_sequence = state.highest_seen_sequence
                session.commit()
                return PersistenceOutcome(rows, acknowledged_sequence)
            except ValueError:
                session.rollback()
                raise
            except SQLAlchemyError as exc:
                session.rollback()
                logger.exception(
                    "Telemetry persistence failed for node=%s sequence=%s channels=%s",
                    node_id,
                    payload.sequence_number,
                    sorted(payload.channels),
                )
                raise TelemetryPersistenceError(str(exc)) from exc

    @staticmethod
    def _sync_state(session: Session, device_id: int, boot_id: str, sequence: int) -> TelemetrySyncState:
        state = session.scalar(select(TelemetrySyncState).where(
            TelemetrySyncState.registered_device_id == device_id,
            TelemetrySyncState.sensor_boot_id == boot_id,
        ))
        if state is None:
            state = TelemetrySyncState(
                registered_device_id=device_id, sensor_boot_id=boot_id,
                first_sequence=sequence, highest_contiguous_sequence=sequence,
                highest_seen_sequence=sequence, missing_sequence_count=0,
            )
            session.add(state)
        return state

    def _update_sync_state(self, session: Session, device_id: int, boot_id: str, sequence: int) -> TelemetrySyncState:
        state = self._sync_state(session, device_id, boot_id, sequence)
        state.highest_seen_sequence = max(state.highest_seen_sequence, sequence)
        while state.highest_contiguous_sequence < state.highest_seen_sequence:
            candidate = state.highest_contiguous_sequence + 1
            exists = session.scalar(select(Reading.id).where(
                Reading.registered_device_id == device_id,
                Reading.sensor_boot_id == boot_id,
                Reading.sequence_number == candidate,
            ).limit(1))
            if exists is None:
                break
            state.highest_contiguous_sequence = candidate
        observed = session.scalar(select(func.count(func.distinct(Reading.sequence_number))).where(
            Reading.registered_device_id == device_id,
            Reading.sensor_boot_id == boot_id,
            Reading.sequence_number >= state.first_sequence,
            Reading.sequence_number <= state.highest_seen_sequence,
        )) or 0
        state.missing_sequence_count = max(0, state.highest_seen_sequence - state.first_sequence + 1 - observed)
        state.updated_at = utc_now()
        return state

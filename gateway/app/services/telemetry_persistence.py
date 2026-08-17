import logging
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from gateway.app.models import Reading, RegisteredDevice, SensorInstallation, utc_now
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.schemas import NormalizedTelemetry
from gateway.app.services.node_capability_service import NodeCapabilityService

logger = logging.getLogger(__name__)


class TelemetryPersistenceError(RuntimeError):
    pass


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
    ) -> list[Reading]:
        with self.session_factory() as session:
            try:
                device = session.scalar(select(RegisteredDevice).where(RegisteredDevice.device_id == node_id))
                if device is None:
                    raise ValueError("registered device no longer exists")
                if device.archived or not device.enabled or device.lifecycle_state == "removed":
                    raise ValueError("registered device is removed or disabled")

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
                session.commit()
                return rows
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

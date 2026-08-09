from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from gateway.app.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class RegisteredDevice(Base):
    __tablename__ = "registered_devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(96), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    device_type: Mapped[str] = mapped_column(String(64), nullable=False, default="xiao_mg24_sense")
    ble_address: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    ble_advertised_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    location: Mapped[str | None] = mapped_column(String(240), nullable=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telemetry_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sensor_package_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    protocol_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    configuration_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    build_identifier: Mapped[str | None] = mapped_column(String(96), nullable=True)
    firmware_git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compatibility_status: Mapped[str] = mapped_column(String(32), nullable=False, default="metadata_missing")
    compatibility_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connection_status: Mapped[str] = mapped_column(String(32), nullable=False, default="disconnected")
    hardware_id: Mapped[str | None] = mapped_column(String(18), nullable=True, index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removal_reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    factory_reset_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_requested")
    readings: Mapped[list["Reading"]] = relationship(back_populates="device")

    @property
    def node_id(self) -> str:
        """Compatibility alias: legacy device_id is the stable MG24 node identity."""
        return self.device_id


class SensorInstallation(Base):
    __tablename__ = "sensor_installations"
    __table_args__ = (
        Index("ix_installations_node_interface", "node_id", "interface_id"),
        UniqueConstraint("device_id", name="uq_installation_device_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("registered_devices.device_id", ondelete="RESTRICT"), nullable=False)
    device_id: Mapped[str] = mapped_column(String(96), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    sensor_profile_id: Mapped[str] = mapped_column(String(160), nullable=False)
    sensor_profile_version: Mapped[str] = mapped_column(String(32), nullable=False)
    interface_id: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    location: Mapped[str | None] = mapped_column(String(240), nullable=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    configuration_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    previous_configuration_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    calibration_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_configured")
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    provisioning_state: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    provisioning_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    active_transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_valid_reading_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProvisioningAttempt(Base):
    __tablename__ = "provisioning_attempts"
    __table_args__ = (Index("ix_provisioning_installation_created", "installation_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    installation_id: Mapped[str] = mapped_column(ForeignKey("sensor_installations.installation_id"), nullable=False)
    requested_configuration_json: Mapped[str] = mapped_column(Text, nullable=False)
    applied_configuration_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class DeviceLifecycleEvent(Base):
    __tablename__ = "device_lifecycle_events"
    __table_args__ = (Index("ix_device_lifecycle_subject_created", "device_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    hardware_id: Mapped[str | None] = mapped_column(String(18), nullable=True)
    ble_address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    connectivity_state: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(48), nullable=False)
    factory_reset_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class NodeFirmwareHistory(Base):
    __tablename__ = "node_firmware_history"
    __table_args__ = (Index("ix_firmware_history_node_seen", "node_id", "last_seen_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    node_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    sensor_package_version: Mapped[str] = mapped_column(String(32), nullable=False)
    firmware_version: Mapped[str] = mapped_column(String(32), nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(32), nullable=False)
    configuration_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    build_identifier: Mapped[str] = mapped_column(String(96), nullable=False)
    git_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compatibility_status: Mapped[str] = mapped_column(String(32), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (
        Index("ix_readings_device_received", "registered_device_id", "received_at"),
        Index("ix_readings_device_channel_received", "registered_device_id", "channel", "received_at"),
        UniqueConstraint("registered_device_id", "session_id", "sequence_number", "channel", name="uq_reading_sequence_channel"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    registered_device_id: Mapped[int] = mapped_column(ForeignKey("registered_devices.id", ondelete="RESTRICT"), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    measured_at_device_uptime: Mapped[int | None] = mapped_column(Integer, nullable=True)
    device_uptime_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sequence_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    record_type: Mapped[str] = mapped_column(String(24), nullable=False, default="measurement")
    channel: Mapped[str] = mapped_column(String(96), nullable=False)
    raw_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalized_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quality: Mapped[str] = mapped_column(String(32), nullable=False, default="good")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    delayed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    installation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    device: Mapped[RegisteredDevice] = relationship(back_populates="readings")

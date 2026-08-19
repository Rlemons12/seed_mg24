from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
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


class GatewayIdentity(Base):
    __tablename__ = "gateway_identity"

    id: Mapped[int] = mapped_column(primary_key=True)
    gateway_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


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


class SensorReregistrationWorkflow(Base):
    """Durable, non-secret state for the reset-and-re-register operator workflow."""

    __tablename__ = "sensor_reregistration_workflows"

    operation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_record_id: Mapped[int] = mapped_column(ForeignKey("registered_devices.id", ondelete="RESTRICT"), nullable=False)
    source_device_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    source_display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    hardware_id: Mapped[str] = mapped_column(String(18), nullable=False, index=True)
    source_ble_address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    state: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    selected_port: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reset_operation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    backup_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    backup_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    registration_choice: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_device_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    target_display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    target_location: Mapped[str | None] = mapped_column(String(240), nullable=True)
    target_ble_address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    configuration_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


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
        Index("ix_readings_device_measured", "registered_device_id", "measured_at"),
        Index("ix_readings_installation_received", "installation_id", "received_at"),
        Index("ix_readings_installation_channel_received", "installation_id", "channel", "received_at"),
        Index("ix_readings_channel_received", "channel", "received_at"),
        Index("ix_readings_gateway_received", "gateway_id", "received_at"),
        UniqueConstraint("registered_device_id", "session_id", "sequence_number", "channel", name="uq_reading_sequence_channel"),
        UniqueConstraint("registered_device_id", "sensor_boot_id", "sequence_number", "channel", name="uq_reading_boot_sequence_channel"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reading_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    gateway_id: Mapped[str] = mapped_column(String(36), nullable=False, default="00000000-0000-0000-0000-000000000000")
    registered_device_id: Mapped[int] = mapped_column(ForeignKey("registered_devices.id", ondelete="RESTRICT"), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    measured_at_device_uptime: Mapped[int | None] = mapped_column(Integer, nullable=True)
    device_uptime_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sequence_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sensor_boot_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    interface_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device: Mapped[RegisteredDevice] = relationship(back_populates="readings")


class TelemetrySyncState(Base):
    __tablename__ = "telemetry_sync_states"
    __table_args__ = (
        UniqueConstraint("registered_device_id", "sensor_boot_id", name="uq_telemetry_sync_device_boot"),
        Index("ix_telemetry_sync_device_updated", "registered_device_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    registered_device_id: Mapped[int] = mapped_column(ForeignKey("registered_devices.id", ondelete="RESTRICT"), nullable=False)
    sensor_boot_id: Mapped[str] = mapped_column(String(16), nullable=False)
    first_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    highest_contiguous_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    highest_seen_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_sequence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class BatteryGeneration(Base):
    __tablename__ = "battery_generations"
    __table_args__ = (
        UniqueConstraint("registered_device_id", "generation_number", name="uq_battery_generation_device_number"),
        Index("ix_battery_generation_device_started", "registered_device_id", "started_at"),
        Index(
            "ux_battery_generation_current_device", "registered_device_id", unique=True,
            sqlite_where=text("ended_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    registered_device_id: Mapped[int] = mapped_column(ForeignKey("registered_devices.id", ondelete="RESTRICT"), nullable=False)
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_reason: Mapped[str] = mapped_column(String(32), nullable=False, default="INITIAL_OBSERVATION")
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class BatteryCycle(Base):
    __tablename__ = "battery_cycles"
    __table_args__ = (
        UniqueConstraint("battery_generation_id", "cycle_number", name="uq_battery_cycle_generation_number"),
        Index("ix_battery_cycle_device_started", "registered_device_id", "started_at"),
        Index("ix_battery_cycle_generation_started", "battery_generation_id", "started_at"),
        Index(
            "ux_battery_cycle_active_device", "registered_device_id", unique=True,
            sqlite_where=text("is_complete = 0"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    registered_device_id: Mapped[int] = mapped_column(ForeignKey("registered_devices.id", ondelete="RESTRICT"), nullable=False)
    battery_generation_id: Mapped[int] = mapped_column(ForeignKey("battery_generations.id", ondelete="RESTRICT"), nullable=False)
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    runtime_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_operating_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unobserved_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    telemetry_records_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sensor_reboot_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vibration_window_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    configuration_change_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    end_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    charge_detection_confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_baseline_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    exclusion_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    runtime_anomaly_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    runtime_health_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    observability_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    protocol_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    configuration_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sensor_boot_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class BatteryDetectorState(Base):
    __tablename__ = "battery_detector_states"

    registered_device_id: Mapped[int] = mapped_column(
        ForeignKey("registered_devices.id", ondelete="RESTRICT"), primary_key=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="DISCHARGING")
    candidate_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    candidate_start_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    peak_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    stable_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_sample_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class BatteryReplacementEvent(Base):
    __tablename__ = "battery_replacement_events"
    __table_args__ = (Index("ix_battery_replacement_device_replaced", "registered_device_id", "replaced_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    registered_device_id: Mapped[int] = mapped_column(ForeignKey("registered_devices.id", ondelete="RESTRICT"), nullable=False)
    old_battery_generation_id: Mapped[int] = mapped_column(ForeignKey("battery_generations.id", ondelete="RESTRICT"), nullable=False)
    new_battery_generation_id: Mapped[int] = mapped_column(ForeignKey("battery_generations.id", ondelete="RESTRICT"), nullable=False)
    replaced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(240), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    previous_runtime_health_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_cycle_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="operator")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class BatteryAlert(Base):
    __tablename__ = "battery_alerts"
    __table_args__ = (
        Index("ix_battery_alert_device_created", "registered_device_id", "created_at"),
        Index("ix_battery_alert_dedupe", "registered_device_id", "alert_type", "last_emitted_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    registered_device_id: Mapped[int] = mapped_column(ForeignKey("registered_devices.id", ondelete="RESTRICT"), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(48), nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    first_emitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_emitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class VibrationWindow(Base):
    __tablename__ = "vibration_windows"
    __table_args__ = (
        Index("ix_vibration_device_observed", "registered_device_id", "observed_at"),
        Index("ix_vibration_installation_observed", "installation_id", "observed_at"),
        UniqueConstraint(
            "registered_device_id", "session_id", "window_sequence", "algorithm_version",
            name="uq_vibration_device_session_window_algorithm",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    window_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    gateway_id: Mapped[str] = mapped_column(String(36), nullable=False)
    registered_device_id: Mapped[int] = mapped_column(ForeignKey("registered_devices.id", ondelete="RESTRICT"), nullable=False)
    installation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    window_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    device_uptime_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm_version: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    configured_sample_rate_hz: Mapped[float] = mapped_column(Float, nullable=False, default=416.0)
    effective_sample_rate_hz: Mapped[float] = mapped_column(Float, nullable=False)
    fft_size: Mapped[int] = mapped_column(Integer, nullable=False, default=256)
    validity: Mapped[str] = mapped_column(String(32), nullable=False)
    accel_rms_x_g: Mapped[float] = mapped_column(Float, nullable=False)
    accel_rms_y_g: Mapped[float] = mapped_column(Float, nullable=False)
    accel_rms_z_g: Mapped[float] = mapped_column(Float, nullable=False)
    accel_peak_x_g: Mapped[float] = mapped_column(Float, nullable=False)
    accel_peak_y_g: Mapped[float] = mapped_column(Float, nullable=False)
    accel_peak_z_g: Mapped[float] = mapped_column(Float, nullable=False)
    crest_x: Mapped[float] = mapped_column(Float, nullable=False)
    crest_y: Mapped[float] = mapped_column(Float, nullable=False)
    crest_z: Mapped[float] = mapped_column(Float, nullable=False)
    kurtosis_x: Mapped[float] = mapped_column(Float, nullable=False)
    kurtosis_y: Mapped[float] = mapped_column(Float, nullable=False)
    kurtosis_z: Mapped[float] = mapped_column(Float, nullable=False)
    dominant_frequency_x_hz: Mapped[float] = mapped_column(Float, nullable=False)
    dominant_frequency_y_hz: Mapped[float] = mapped_column(Float, nullable=False)
    dominant_frequency_z_hz: Mapped[float] = mapped_column(Float, nullable=False)
    dominant_amplitude_x_g: Mapped[float] = mapped_column(Float, nullable=False)
    dominant_amplitude_y_g: Mapped[float] = mapped_column(Float, nullable=False)
    dominant_amplitude_z_g: Mapped[float] = mapped_column(Float, nullable=False)
    gyro_rms_x_dps: Mapped[float] = mapped_column(Float, nullable=False)
    gyro_rms_y_dps: Mapped[float] = mapped_column(Float, nullable=False)
    gyro_rms_z_dps: Mapped[float] = mapped_column(Float, nullable=False)


class VibrationBaseline(Base):
    __tablename__ = "vibration_baselines"
    __table_args__ = (
        UniqueConstraint("registered_device_id", "installation_id", "algorithm_version", name="uq_vibration_baseline_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    registered_device_id: Mapped[int] = mapped_column(ForeignKey("registered_devices.id", ondelete="RESTRICT"), nullable=False, index=True)
    installation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    baseline_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    algorithm_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="building")
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    minimum_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    statistics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_window_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    established_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    last_relearn_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class VibrationBaselineHistory(Base):
    __tablename__ = "vibration_baseline_history"
    __table_args__ = (
        UniqueConstraint("registered_device_id", "installation_id", "algorithm_version", "baseline_version",
                         name="uq_vibration_baseline_history_version"),
        Index("ix_vibration_baseline_history_device_created", "registered_device_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    registered_device_id: Mapped[int] = mapped_column(ForeignKey("registered_devices.id", ondelete="RESTRICT"), nullable=False)
    installation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    baseline_version: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="superseded")
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    statistics_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    established_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    reason: Mapped[str | None] = mapped_column(String(240), nullable=True)


class VibrationCondition(Base):
    __tablename__ = "vibration_conditions"

    id: Mapped[int] = mapped_column(primary_key=True)
    registered_device_id: Mapped[int] = mapped_column(ForeignKey("registered_devices.id", ondelete="RESTRICT"), nullable=False, unique=True)
    installation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    baseline_id: Mapped[int | None] = mapped_column(ForeignKey("vibration_baselines.id", ondelete="SET NULL"), nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="BASELINE_PENDING")
    baseline_similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    factors_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    pending_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pending_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latest_window_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

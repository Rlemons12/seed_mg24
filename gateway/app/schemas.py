from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiError(BaseModel):
    error: str
    detail: str


class Discovery(BaseModel):
    address: str
    name: str | None = None
    rssi: int | None = None
    service_uuids: list[str] = Field(default_factory=list, max_length=32)
    compatible: bool = False
    compatibility_reason: str
    stable_device_id: str | None = None
    temporary_id: str | None = Field(default=None, max_length=160)
    last_seen_at: datetime


class DeviceCreate(BaseModel):
    device_id: str = Field(min_length=1, max_length=96)
    display_name: str = Field(min_length=1, max_length=160)
    discovery_address: str = Field(min_length=1, max_length=128)
    device_type: str = Field(default="xiao_mg24_sense", min_length=1, max_length=64)
    location: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=1000)
    allow_incompatible: bool = False

    @field_validator("device_id", "display_name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class DeviceUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    location: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=1000)
    enabled: bool | None = None

    @field_validator("display_name")
    @classmethod
    def display_name_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("display_name must not be blank")
        return value.strip() if value is not None else None


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: str
    display_name: str
    device_type: str
    ble_address: str | None
    ble_advertised_name: str | None
    enabled: bool
    archived: bool
    location: str | None
    description: str | None
    firmware_version: str | None
    telemetry_schema_version: int | None
    sensor_package_version: str | None
    protocol_version: str | None
    configuration_schema_version: int | None
    build_identifier: str | None
    firmware_git_commit: str | None
    compatibility_status: str
    compatibility_message: str | None
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None
    last_connected_at: datetime | None
    connection_status: str
    hardware_id: str | None
    lifecycle_state: str
    removed_at: datetime | None
    removal_reason: str | None
    factory_reset_status: str
    last_error: str | None = None
    reporting_mode: Literal["EDGE_SUMMARY", "LIVE", "LOW_POWER", "UNKNOWN"] = "UNKNOWN"
    rssi: int | None = None


class LifecycleConfirmationRequest(BaseModel):
    operation: Literal["remove", "restore"]
    device_id: str = Field(min_length=1, max_length=96)
    expected_hardware_id: str | None = Field(default=None, pattern=r"^0x[0-9A-F]{16}$")
    expected_ble_address: str | None = Field(default=None, min_length=3, max_length=128)


class LifecycleConfirmationResponse(BaseModel):
    confirmation_token: str
    operation: Literal["remove", "restore"]
    device_id: str
    display_name: str
    hardware_id: str | None
    ble_address: str | None
    connection_status: str
    expires_in_seconds: int


class LifecycleExecuteRequest(BaseModel):
    confirmation_token: str = Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")
    operation: Literal["remove", "restore"]
    device_id: str = Field(min_length=1, max_length=96)
    expected_hardware_id: str | None = Field(default=None, pattern=r"^0x[0-9A-F]{16}$")
    expected_ble_address: str | None = Field(default=None, min_length=3, max_length=128)
    reason: str | None = Field(default=None, max_length=240)


class LifecycleOperationResponse(BaseModel):
    operation_id: str
    status: str
    device_id: str
    lifecycle_state: str
    already_applied: bool
    telemetry_preserved: bool
    physical_sensor_changed: bool


class ChannelValue(BaseModel):
    value: float | int | bool | str | None = None
    raw_value: float | int | bool | None = None
    unit: str | None = Field(default=None, max_length=32)
    quality: Literal["good", "estimated", "uncalibrated", "invalid", "sensor_fault", "stale"] = "good"
    value_kind: Literal["raw", "filtered", "calibrated", "derived", "state", "health"] = "raw"


class VibrationSummary(BaseModel):
    schema_version: Literal[1] = 1
    algorithm_version: Literal[1] = 1
    window_sequence: int = Field(ge=1, le=0xFFFFFFFF)
    device_uptime_ms: int = Field(ge=0, le=0xFFFFFFFF)
    effective_sample_rate_hz: float = Field(gt=0, le=500)
    validity: Literal["valid", "invalid"]
    accel_rms_g: tuple[float, float, float]
    accel_peak_g: tuple[float, float, float]
    crest_factor: tuple[float, float, float]
    kurtosis: tuple[float, float, float]
    dominant_frequency_hz: tuple[float, float, float]
    dominant_amplitude_g: tuple[float, float, float]
    gyro_rms_dps: tuple[float, float, float]


class NormalizedTelemetry(BaseModel):
    schema_version: int = Field(ge=1, le=255)
    device_id: str | None = Field(default=None, max_length=96)
    device_type: str = Field(default="xiao_mg24_sense", max_length=64)
    record_type: Literal["measurement", "event", "heartbeat", "config_ack", "config_error", "burst_fragment", "vibration"]
    device_uptime_ms: int | None = Field(default=None, ge=0, le=0xFFFFFFFF)
    sequence_number: int | None = Field(default=None, ge=0, le=0xFFFFFFFF)
    sensor_boot_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    sample_count: int | None = Field(default=None, ge=1, le=0xFFFFFFFF)
    received_at: datetime
    delayed: bool = False
    event: str | None = Field(default=None, max_length=96)
    channels: dict[str, ChannelValue] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    vibration: VibrationSummary | None = None
    original_payload: dict[str, Any]


class ReadingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reading_uuid: str
    gateway_id: str
    received_at: datetime
    measured_at: datetime | None
    measured_at_device_uptime: int | None
    device_uptime_ms: int | None
    sequence_number: int | None
    sensor_boot_id: str | None
    sample_count: int | None
    session_id: str
    record_type: str
    channel: str
    raw_value: float | None
    normalized_value: float | None
    unit: str | None
    quality: str
    delayed: bool
    installation_id: str | None
    interface_id: str | None

    @field_validator("received_at", "measured_at", mode="after")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ReadingPage(BaseModel):
    items: list[ReadingResponse]
    total: int | None
    offset: int
    limit: int
    next_cursor: str | None = None


class CommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=32)


class CommandResponse(BaseModel):
    accepted: bool
    command: str


class WebSocketEvent(BaseModel):
    event: Literal["telemetry", "device_status", "discovery"]
    device_id: str | None = None
    timestamp: datetime
    data: dict[str, Any]


class NodeInterface(BaseModel):
    interface_id: str = Field(min_length=1, max_length=64)
    type: Literal["analog", "built_in", "i2c", "spi", "uart", "digital"]
    capabilities: list[str] = Field(min_length=1, max_length=32)
    telemetry_channels: list[str] = Field(default_factory=list, max_length=16)
    exclusive: bool = True
    configuration_supported: bool = False
    configuration_persistence: Literal["none", "volatile", "persistent"] = "none"


class NodeCapabilities(BaseModel):
    schema_version: Literal[1] = 1
    node_id: str
    firmware_version: str | None = None
    interfaces: list[NodeInterface]
    filters: list[str]
    reporting_modes: list[str]
    minimum_interval_ms: int = 50
    maximum_interval_ms: int = 5000
    configuration_readback: bool = False


class HardwareInformation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manufacturer: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    datasheet: str | None = Field(default=None, max_length=500)
    electrical_interface: str | None = Field(default=None, max_length=240)
    measurement_range: str | None = Field(default=None, max_length=240)
    signal_conditioning: str | None = Field(default=None, max_length=500)


class InstallationConfiguration(BaseModel):
    sample_interval_ms: int = Field(default=100, ge=50, le=5000)
    processing_interval_ms: int = Field(default=100, ge=50, le=5000)
    report_interval_ms: int = Field(default=100, ge=50, le=5000)
    heartbeat_interval_ms: int = Field(default=30000, ge=1000, le=3600000)
    filter_type: Literal["none", "ema", "moving_average", "median", "digital_debounce"] = "none"
    filter_window: int = Field(default=1, ge=1, le=9)
    change_deadband: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    calibration_enabled: bool = False
    calibration_gain: float | None = Field(default=None, allow_inf_nan=False)
    calibration_offset: float | None = Field(default=None, allow_inf_nan=False)
    alarms_enabled: bool = False
    warning_low: float | None = Field(default=None, allow_inf_nan=False)
    warning_high: float | None = Field(default=None, allow_inf_nan=False)
    alarm_low: float | None = Field(default=None, allow_inf_nan=False)
    alarm_high: float | None = Field(default=None, allow_inf_nan=False)
    hardware_information: HardwareInformation | None = None


class InstallationCreate(BaseModel):
    node_id: str = Field(min_length=1, max_length=96)
    device_id: str = Field(min_length=1, max_length=96)
    display_name: str = Field(min_length=1, max_length=160)
    sensor_profile_id: str = Field(min_length=1, max_length=160)
    sensor_profile_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    interface_id: str = Field(min_length=1, max_length=64)
    location: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=1000)
    configuration: InstallationConfiguration

    @field_validator("device_id", "display_name")
    @classmethod
    def installation_names_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class InstallationUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    location: str | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=1000)
    configuration: InstallationConfiguration | None = None


class InstallationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    installation_id: str
    node_id: str
    device_id: str
    display_name: str
    sensor_profile_id: str
    sensor_profile_version: str
    interface_id: str
    enabled: bool
    archived: bool
    location: str | None
    description: str | None
    configuration: InstallationConfiguration
    calibration_status: str
    verification_status: str
    provisioning_state: str
    provisioning_error: str | None
    created_at: datetime
    updated_at: datetime
    last_seen_at: datetime | None
    last_valid_reading_at: datetime | None


class ProfileUpgradeRequest(BaseModel):
    profile_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")


class ProfileValidationRequest(BaseModel):
    profile: dict[str, Any]

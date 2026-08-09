from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FiniteFloat = Annotated[float, Field(allow_inf_nan=False, ge=-1e12, le=1e12)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProfileStatus(StrEnum):
    DRAFT = "draft"
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class Provenance(StrictModel):
    source: str = Field(min_length=1, max_length=240)
    verified: bool = False
    verified_by: str | None = Field(default=None, max_length=160)
    verified_at: str | None = Field(default=None, max_length=64)
    reference: str | None = Field(default=None, max_length=500)


class InterfaceDefinition(StrictModel):
    type: Literal["analog", "built_in", "i2c", "spi", "uart", "digital"]
    supported_inputs: list[str] = Field(min_length=1, max_length=16)
    supply_voltage: FiniteFloat | None = None
    signal_min: FiniteFloat | None = None
    signal_max: FiniteFloat | None = None
    required_signal_conditioning: str | None = Field(default=None, max_length=1000)
    wiring_notes: list[str] = Field(default_factory=list, max_length=20)
    exclusive: bool = True

    @model_validator(mode="after")
    def valid_signal_range(self):
        if self.signal_min is not None and self.signal_max is not None and self.signal_min >= self.signal_max:
            raise ValueError("signal_min must be less than signal_max")
        return self


class MeasurementChannelDefinition(StrictModel):
    channel_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    quantity: str | None = Field(default=None, max_length=64)
    raw_data_type: Literal["integer", "float", "boolean"]
    raw_unit: Literal["adc_count", "digital_state", "g", "dps", "V", "percent"]
    engineering_unit: str | None = Field(default=None, max_length=32)
    range_min: FiniteFloat | None = None
    range_max: FiniteFloat | None = None
    value_kind: Literal["raw", "filtered", "calibrated", "derived"] = "raw"

    @model_validator(mode="after")
    def valid_range(self):
        if self.range_min is not None and self.range_max is not None and self.range_min >= self.range_max:
            raise ValueError("measurement range_min must be less than range_max")
        return self


class ConversionDefinition(StrictModel):
    type: Literal["unconfigured", "identity", "linear", "piecewise_linear", "polynomial", "lookup_table"]
    gain: FiniteFloat | None = None
    offset: FiniteFloat | None = None
    coefficients: list[FiniteFloat] | None = Field(default=None, max_length=6)
    points: list[tuple[FiniteFloat, FiniteFloat]] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_parameters(self):
        if self.type == "linear" and (self.gain is None or self.offset is None):
            raise ValueError("linear conversion requires gain and offset")
        if self.type == "polynomial" and (not self.coefficients or len(self.coefficients) > 6):
            raise ValueError("polynomial conversion requires one to six coefficients")
        if self.type in {"piecewise_linear", "lookup_table"}:
            if not self.points or len(self.points) < 2:
                raise ValueError(f"{self.type} conversion requires at least two points")
            if any(self.points[index][0] >= self.points[index + 1][0] for index in range(len(self.points) - 1)):
                raise ValueError("conversion point inputs must be strictly increasing")
        if self.type in {"unconfigured", "identity"} and any(
            value is not None for value in (self.gain, self.offset, self.coefficients, self.points)
        ):
            raise ValueError(f"{self.type} conversion does not accept parameters")
        return self


class SamplingDefinition(StrictModel):
    minimum_interval_ms: int | None = Field(default=None, ge=10, le=3600000)
    default_interval_ms: int | None = Field(default=None, ge=10, le=3600000)
    maximum_interval_ms: int | None = Field(default=None, ge=10, le=3600000)

    @model_validator(mode="after")
    def ordered(self):
        values = (self.minimum_interval_ms, self.default_interval_ms, self.maximum_interval_ms)
        if all(value is not None for value in values) and not values[0] <= values[1] <= values[2]:
            raise ValueError("sampling intervals must be ordered minimum <= default <= maximum")
        return self


class FilterDefinition(StrictModel):
    supported: list[Literal["none", "ema", "moving_average", "median", "digital_debounce"]] = Field(min_length=1, max_length=5)
    default: Literal["none", "ema", "moving_average", "median", "digital_debounce"] = "none"
    maximum_window: int = Field(default=9, ge=1, le=9)

    @model_validator(mode="after")
    def default_supported(self):
        if self.default not in self.supported:
            raise ValueError("default filter must be supported")
        return self


class AlarmDefinition(StrictModel):
    supported: bool = False
    defaults_enabled: bool = False
    warning_low: FiniteFloat | None = None
    warning_high: FiniteFloat | None = None
    alarm_low: FiniteFloat | None = None
    alarm_high: FiniteFloat | None = None

    @model_validator(mode="after")
    def safe_defaults(self):
        values = (self.warning_low, self.warning_high, self.alarm_low, self.alarm_high)
        if self.defaults_enabled and (not self.supported or all(value is None for value in values)):
            raise ValueError("enabled alarm defaults require supported, documented threshold values")
        if not self.defaults_enabled and any(value is not None for value in values):
            raise ValueError("threshold values require defaults_enabled=true")
        return self


class FirmwareRequirement(StrictModel):
    minimum_version: str | None = Field(default=None, pattern=r"^\d+\.\d+\.\d+$")
    required_capabilities: list[str] = Field(default_factory=list, max_length=32)


class SensorProfile(StrictModel):
    schema_version: Literal[1]
    profile_id: str = Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$", max_length=160)
    profile_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    manufacturer: str = Field(min_length=1, max_length=160)
    model: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    category: str = Field(min_length=1, max_length=64)
    datasheet_reference: str | None = Field(default=None, max_length=500)
    status: Literal["draft", "unverified", "verified", "deprecated", "disabled"]
    interface: InterfaceDefinition
    measurement_channels: list[MeasurementChannelDefinition] = Field(min_length=1, max_length=16)
    conversion: ConversionDefinition
    sampling: SamplingDefinition
    filter: FilterDefinition
    alarms: AlarmDefinition
    firmware: FirmwareRequirement
    provenance: Provenance
    deprecated_by: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def semantic_rules(self):
        channel_ids = [channel.channel_id for channel in self.measurement_channels]
        if len(channel_ids) != len(set(channel_ids)):
            raise ValueError("measurement channel IDs must be unique")
        calibrated = self.conversion.type not in {"unconfigured", "identity"}
        if calibrated and any(channel.engineering_unit is None for channel in self.measurement_channels):
            raise ValueError("configured conversion requires engineering units on every measurement channel")
        if self.status == ProfileStatus.VERIFIED:
            if not self.provenance.verified or not self.provenance.verified_by or not self.provenance.reference:
                raise ValueError("verified profiles require verified provenance, verifier, and reference")
        if self.status == ProfileStatus.DEPRECATED and not self.deprecated_by:
            raise ValueError("deprecated profiles require deprecated_by")
        return self

    @property
    def catalog_key(self) -> tuple[str, str]:
        return self.profile_id, self.profile_version

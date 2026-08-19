from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPOSITORY_ENV_FILE, env_prefix="SEED_MG24_", extra="ignore")

    database_url: str = "sqlite:///./data/seed_mg24.db"
    host: str = "0.0.0.0"
    port: int = Field(8000, ge=1, le=65535)
    log_level: str = "INFO"
    device_id_pattern: str = r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$"
    scan_duration_seconds: float = Field(8.0, ge=1.0, le=60.0)
    discovery_ttl_seconds: float = Field(120.0, ge=10.0, le=3600.0)
    max_connection_attempts: int = Field(3, ge=1, le=20)
    reconnect_initial_seconds: float = Field(1.0, ge=0.1, le=60.0)
    reconnect_max_seconds: float = Field(60.0, ge=1.0, le=3600.0)
    reconnect_stable_seconds: float = Field(30.0, ge=1.0, le=3600.0)
    stale_after_seconds: float = Field(30.0, ge=1.0, le=86400.0)
    poll_interval_seconds: float = Field(1.0, ge=0.2, le=60.0)
    max_payload_bytes: int = Field(2048, ge=128, le=65536)
    max_payload_json_bytes: int = Field(4096, ge=256, le=65536)
    history_page_size_max: int = Field(500, ge=10, le=5000)
    history_max_days: int = Field(31, ge=1, le=366)
    history_retention_days: int | None = Field(default=None, ge=1, le=36500)
    history_retention_batch_size: int = Field(1000, ge=100, le=10000)
    vibration_baseline_minimum_windows: int = Field(100, ge=20, le=10000)
    vibration_condition_persistence_windows: int = Field(3, ge=1, le=100)
    vibration_persistence_interval_seconds: float = Field(5.0, ge=0.5, le=3600)
    battery_minimum_voltage_rise: float = Field(0.12, gt=0, le=5)
    battery_voltage_noise_floor: float = Field(0.02, ge=0, le=1)
    battery_charge_confirmation_seconds: float = Field(120.0, ge=1, le=86400)
    battery_charge_minimum_samples: int = Field(3, ge=2, le=1000)
    battery_stable_voltage_seconds: float = Field(300.0, ge=1, le=86400)
    battery_maximum_sample_gap_seconds: float = Field(900.0, ge=1, le=604800)
    battery_baseline_minimum_cycles: int = Field(5, ge=2, le=100)
    battery_aging_runtime_ratio: float = Field(0.90, gt=0, le=1)
    battery_plan_replacement_runtime_ratio: float = Field(0.75, gt=0, le=1)
    battery_replace_runtime_ratio: float = Field(0.60, gt=0, le=1)
    battery_required_degraded_cycles: int = Field(3, ge=2, le=20)
    battery_maximum_unobserved_ratio: float = Field(0.25, ge=0, lt=1)
    battery_recharge_warning_seconds: float = Field(172800.0, ge=60, le=31536000)
    battery_trend_lookback_cycles: int = Field(6, ge=3, le=50)
    battery_alert_cooldown_seconds: float = Field(86400.0, ge=60, le=31536000)
    battery_low_voltage_warning: float | None = Field(default=None, gt=0, le=10)
    battery_low_voltage_critical: float | None = Field(default=None, gt=0, le=10)
    gateway_id: str | None = None
    sensor_profile_directory: Path = Path("./data/sensor_profiles")
    max_profile_upload_bytes: int = Field(65536, ge=1024, le=1048576)
    provisioning_timeout_seconds: float = Field(10.0, ge=1.0, le=120.0)
    firmware_catalog_path: Path = Path("./sensor_package/approved_firmware.json")
    arduino_cli: str = "arduino-cli"
    developer_firmware_approval: bool = False
    gateway_instance_lock: bool = True

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported log level")
        return value

    @field_validator(
        "history_retention_days", "gateway_id", "battery_low_voltage_warning", "battery_low_voltage_critical",
        mode="before",
    )
    @classmethod
    def blank_is_disabled(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_battery_policy(self):
        if not (
            self.battery_replace_runtime_ratio
            < self.battery_plan_replacement_runtime_ratio
            <= self.battery_aging_runtime_ratio
        ):
            raise ValueError("battery runtime thresholds must satisfy replace < plan replacement <= aging")
        if (
            self.battery_low_voltage_warning is not None
            and self.battery_low_voltage_critical is not None
            and self.battery_low_voltage_critical >= self.battery_low_voltage_warning
        ):
            raise ValueError("battery critical voltage must be below warning voltage")
        return self

    def ensure_runtime_directories(self) -> None:
        if self.database_url.startswith("sqlite:///"):
            path = Path(self.database_url.removeprefix("sqlite:///"))
            if str(path) != ":memory:":
                path.parent.mkdir(parents=True, exist_ok=True)
        self.sensor_profile_directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()

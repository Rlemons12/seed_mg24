from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SEED_MG24_", extra="ignore")

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
    sensor_profile_directory: Path = Path("./data/sensor_profiles")
    max_profile_upload_bytes: int = Field(65536, ge=1024, le=1048576)
    provisioning_timeout_seconds: float = Field(10.0, ge=1.0, le=120.0)

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported log level")
        return value

    def ensure_runtime_directories(self) -> None:
        if self.database_url.startswith("sqlite:///"):
            path = Path(self.database_url.removeprefix("sqlite:///"))
            if str(path) != ":memory:":
                path.parent.mkdir(parents=True, exist_ok=True)
        self.sensor_profile_directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()

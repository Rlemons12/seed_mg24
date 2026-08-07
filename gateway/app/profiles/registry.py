import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from pydantic import ValidationError

from gateway.app.profiles.models import ProfileStatus, SensorProfile


@dataclass(frozen=True)
class ProfileLoadError:
    path: str
    error: str


class DuplicateProfileError(ValueError):
    pass


class ProfileRegistry:
    def __init__(self, directory: Path, bundled_directory: Path | None = None, max_upload_bytes: int = 65536) -> None:
        self.directory = directory
        self.bundled_directory = bundled_directory
        self.max_upload_bytes = max_upload_bytes
        self._profiles: dict[tuple[str, str], SensorProfile] = {}
        self.errors: list[ProfileLoadError] = []

    @staticmethod
    def parse(data: bytes | str) -> SensorProfile:
        raw = data.encode() if isinstance(data, str) else data
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("profile must be UTF-8 JSON data") from exc
        return SensorProfile.model_validate(ProfileRegistry.migrate_schema(value))

    @staticmethod
    def migrate_schema(value: object) -> dict:
        if not isinstance(value, dict):
            raise ValueError("profile root must be an object")
        version = value.get("schema_version")
        if version == 1:
            return value
        raise ValueError(f"unsupported profile schema version: {version}")

    def reload(self) -> None:
        loaded: dict[tuple[str, str], SensorProfile] = {}
        errors: list[ProfileLoadError] = []
        roots = [root for root in (self.bundled_directory, self.directory) if root and root.exists()]
        for root in roots:
            for path in sorted(root.glob("*.json")):
                try:
                    data = path.read_bytes()
                    if len(data) > self.max_upload_bytes:
                        raise ValueError("profile file exceeds configured maximum")
                    profile = self.parse(data)
                    if profile.catalog_key in loaded:
                        raise DuplicateProfileError(f"duplicate profile ID and version: {profile.catalog_key}")
                    loaded[profile.catalog_key] = profile
                except (OSError, ValueError, ValidationError) as exc:
                    errors.append(ProfileLoadError(str(path), str(exc)[:1000]))
        self._profiles = loaded
        self.errors = errors

    def list(
        self,
        *,
        manufacturer: str | None = None,
        model: str | None = None,
        category: str | None = None,
        interface_type: str | None = None,
        include_disabled: bool = False,
    ) -> list[SensorProfile]:
        values = self._profiles.values()
        return sorted(
            (
                profile
                for profile in values
                if (include_disabled or profile.status != ProfileStatus.DISABLED)
                and (not manufacturer or manufacturer.casefold() in profile.manufacturer.casefold())
                and (not model or model.casefold() in profile.model.casefold())
                and (not category or category.casefold() == profile.category.casefold())
                and (not interface_type or interface_type == profile.interface.type)
            ),
            key=lambda item: (item.manufacturer.casefold(), item.model.casefold(), item.profile_version),
        )

    def get(self, profile_id: str, version: str | None = None) -> SensorProfile | None:
        matches = [profile for (candidate_id, _), profile in self._profiles.items() if candidate_id == profile_id]
        if version is not None:
            return self._profiles.get((profile_id, version))
        return (
            sorted(matches, key=lambda item: tuple(int(part) for part in item.profile_version.split(".")), reverse=True)[0]
            if matches
            else None
        )

    def import_profile(self, data: bytes) -> SensorProfile:
        if len(data) > self.max_upload_bytes:
            raise ValueError("profile upload exceeds configured maximum")
        profile = self.parse(data)
        if profile.catalog_key in self._profiles:
            raise DuplicateProfileError("profile ID and version already exist")
        self.directory.mkdir(parents=True, exist_ok=True)
        filename = f"{profile.profile_id}-{profile.profile_version}.json"
        target = self.directory / filename
        if target.exists():
            raise DuplicateProfileError("profile file already exists")
        with NamedTemporaryFile("wb", dir=self.directory, delete=False) as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(target)
        self.reload()
        return profile

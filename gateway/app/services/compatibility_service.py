import json
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version


@dataclass(frozen=True)
class CompatibilityResult:
    status: str
    message: str


class CompatibilityService:
    def __init__(self, matrix_path: Path) -> None:
        self.matrix = json.loads(matrix_path.read_text(encoding="utf-8"))

    def evaluate(self, metadata: dict) -> CompatibilityResult:
        required = (
            "sensor_package_version", "firmware_version", "protocol_version",
            "configuration_schema_version", "build_identifier",
        )
        if any(metadata.get(key) in (None, "") for key in required):
            return CompatibilityResult(
                "metadata_missing", "Node metadata is incomplete; diagnostics remain available but configuration is blocked."
            )
        if metadata["protocol_version"] not in self.matrix["supported_protocol_versions"]:
            return CompatibilityResult("protocol_unsupported", f"Protocol {metadata['protocol_version']} is not supported by this gateway.")
        try:
            sensor = Version(metadata["sensor_package_version"])
            limits = self.matrix["supported_sensor_package_versions"]
            if sensor < Version(limits["minimum"]):
                return CompatibilityResult(
                    "sensor_update_required", f"Sensor package {sensor} is older than supported minimum {limits['minimum']}."
                )
            if sensor >= Version(limits["maximum_exclusive"]):
                return CompatibilityResult("gateway_update_required", f"Sensor package {sensor} requires a newer gateway.")
        except InvalidVersion:
            return CompatibilityResult("unknown", "Node supplied an invalid semantic version.")
        return CompatibilityResult("compatible", "Firmware and protocol versions are compatible.")

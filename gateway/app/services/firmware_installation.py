import asyncio
import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from uuid import uuid4

from serial.tools import list_ports


class FirmwareValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


def inspect_intel_hex(path: Path) -> tuple[int, int, int]:
    addresses: list[tuple[int, int]] = []
    base = 0
    for number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        if not line.startswith(":"):
            raise FirmwareValidationError(f"invalid Intel HEX record at line {number}")
        raw = bytes.fromhex(line[1:])
        if len(raw) < 5 or (sum(raw) & 0xFF):
            raise FirmwareValidationError(f"invalid Intel HEX checksum at line {number}")
        count, offset, kind = raw[0], int.from_bytes(raw[1:3], "big"), raw[3]
        if count != len(raw) - 5:
            raise FirmwareValidationError(f"invalid Intel HEX length at line {number}")
        if kind == 0:
            addresses.append((base + offset, base + offset + count - 1))
        elif kind == 4:
            base = int.from_bytes(raw[4:6], "big") << 16
        elif kind not in {1, 2, 3, 5}:
            raise FirmwareValidationError(f"unsupported Intel HEX record type {kind}")
    if not addresses:
        raise FirmwareValidationError("firmware image contains no programmed data")
    return min(start for start, _ in addresses), max(end for _, end in addresses), sum(end - start + 1 for start, end in addresses)


class ApprovedFirmwareCatalog:
    def __init__(self, repository_root: Path, manifest_path: Path, *, developer_approval_enabled: bool = False) -> None:
        self.repository_root = repository_root.resolve()
        self.manifest_path = manifest_path.resolve()
        self._packages = {item["package_id"]: item for item in json.loads(self.manifest_path.read_text(encoding="utf-8"))["packages"]}
        self.developer_approval_enabled = developer_approval_enabled
        self._developer_approvals: dict[str, dict] = {}

    def list(self) -> list[dict]:
        packages = []
        for item in self._packages.values():
            public = {key: value for key, value in item.items() if key != "artifact_path"}
            try:
                self.validate(item["package_id"])
            except FirmwareValidationError as exc:
                public.update(status=exc.code, status_message=str(exc))
            else:
                if item["package_id"] in self._developer_approvals:
                    public.update(status="developer_ready", status_message="Development firmware approved for this server session")
                    public.update(self._developer_approvals[item["package_id"]])
                else:
                    public.update(status="ready", status_message="Approved firmware artifact verified")
            public["developer_approval_available"] = self.developer_approval_enabled and public["status"] in {
                "hash_mismatch", "missing", "unreadable"
            }
            packages.append(public)
        return packages

    def approve_development(self, package_id: str) -> dict:
        if not self.developer_approval_enabled:
            raise FirmwareValidationError("developer firmware approval is disabled", code="disabled")
        item = self._packages.get(package_id)
        if item is None:
            raise FirmwareValidationError("unknown firmware package")
        if not item.get("application_only") or item.get("fqbn") != "SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs":
            raise FirmwareValidationError("firmware package is not eligible for development approval")
        path = (self.repository_root / item["artifact_path"]).resolve()
        if self.repository_root not in path.parents or path.name != item.get("artifact_filename"):
            raise FirmwareValidationError("firmware artifact path is outside the approved catalog")
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise FirmwareValidationError("development firmware artifact is missing", code="missing") from exc
        except OSError as exc:
            raise FirmwareValidationError("development firmware artifact cannot be read", code="unreadable") from exc
        start, end, _ = inspect_intel_hex(path)
        if start <= int(item["bootloader_end"], 16) or end >= int(item["nvm3_start"], 16):
            raise FirmwareValidationError("development firmware overlaps a protected region")
        self._developer_approvals[package_id] = {
            "artifact_size": len(data),
            "sha256": hashlib.sha256(data).hexdigest().upper(),
            "application_start": f"0x{start:08X}",
            "application_end": f"0x{end:08X}",
        }
        return next(item for item in self.list() if item["package_id"] == package_id)

    def validate(self, package_id: str) -> tuple[dict, Path]:
        item = self._packages.get(package_id)
        if item is None:
            raise FirmwareValidationError("unknown firmware package")
        required = {"package_id", "firmware_version", "protocol_version", "fqbn", "artifact_path", "artifact_filename",
                    "artifact_size", "sha256", "application_start", "application_end", "bootloader_end", "nvm3_start",
                    "build_provenance", "application_only"}
        if not required.issubset(item) or not item["build_provenance"]:
            raise FirmwareValidationError("firmware package provenance is incomplete")
        if not item["application_only"] or "with_bootloader" in item["artifact_filename"].lower():
            raise FirmwareValidationError("bootloader-containing firmware is not approved")
        if item["fqbn"] != "SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs":
            raise FirmwareValidationError("firmware package targets an unsupported board")
        path = (self.repository_root / item["artifact_path"]).resolve()
        if self.repository_root not in path.parents or path.name != item["artifact_filename"]:
            raise FirmwareValidationError("firmware artifact path is outside the approved catalog")
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise FirmwareValidationError("approved firmware artifact is missing", code="missing") from exc
        except OSError as exc:
            raise FirmwareValidationError("approved firmware artifact cannot be read", code="unreadable") from exc
        expected = self._developer_approvals.get(package_id, item)
        if len(data) != expected["artifact_size"] or hashlib.sha256(data).hexdigest().upper() != expected["sha256"].upper():
            raise FirmwareValidationError("firmware artifact hash or size mismatch", code="hash_mismatch")
        start, end, _ = inspect_intel_hex(path)
        if start != int(expected["application_start"], 16) or end != int(expected["application_end"], 16):
            raise FirmwareValidationError("firmware programmed range does not match the approved manifest")
        if start <= int(item["bootloader_end"], 16) or end >= int(item["nvm3_start"], 16):
            raise FirmwareValidationError("firmware overlaps a protected region")
        return item, path


@dataclass
class FirmwareOperation:
    operation_id: str
    hardware_serial: str
    package_id: str
    state: str = "queued"
    progress: list[str] = field(default_factory=list)
    error: str | None = None
    started: float = field(default_factory=monotonic)


class UsbFirmwareInstaller:
    VID, PID = 0x2886, 0x0062

    def __init__(self, catalog: ApprovedFirmwareCatalog, repository_root: Path, cli: str = "arduino-cli") -> None:
        self.catalog = catalog
        self.repository_root = repository_root
        self.cli = cli
        self.operations: dict[str, FirmwareOperation] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def boards(self) -> list[dict]:
        rows = []
        compatible_packages = [
            item["package_id"] for item in self.catalog.list() if item["status"] in {"ready", "developer_ready"}
        ]
        for port in list_ports.comports():
            if port.vid == self.VID and port.pid == self.PID:
                rows.append({"hardware_serial": port.serial_number, "board_type": "Seeed Studio XIAO MG24 Sense",
                             "usb_vid_pid": "2886:0062", "com_port": port.device, "cmsis_dap": True,
                             "application_state": "unknown", "busy": False,
                             "compatible_packages": compatible_packages})
        return rows

    def start(self, hardware_serial: str, package_id: str) -> FirmwareOperation:
        if not re.fullmatch(r"[A-F0-9]{8,32}", hardware_serial):
            raise FirmwareValidationError("hardware serial is invalid")
        self.catalog.validate(package_id)
        matches = [row for row in self.boards() if row["hardware_serial"] == hardware_serial]
        if len(matches) != 1:
            raise FirmwareValidationError("exactly one matching supported board is required")
        lock = self._locks.setdefault(hardware_serial, asyncio.Lock())
        if lock.locked():
            raise FirmwareValidationError("device is busy")
        operation = FirmwareOperation(uuid4().hex, hardware_serial, package_id)
        self.operations[operation.operation_id] = operation
        asyncio.create_task(self._run(operation, matches[0]["com_port"], lock))
        return operation

    async def _run(self, operation: FirmwareOperation, port: str, lock: asyncio.Lock) -> None:
        async with lock:
            try:
                item, artifact = self.catalog.validate(operation.package_id)
                operation.state = "uploading"
                operation.progress.append("package_validated")
                executable = shutil.which(self.cli)
                if not executable:
                    raise FirmwareValidationError("arduino-cli is unavailable")
                sketch = self.repository_root / "sensor_package" / "firmware" / "xiao_mg24_sensor_node"
                command = [executable, "upload", "--port", port, "--fqbn", item["fqbn"], "--input-dir", str(artifact.parent), str(sketch)]
                process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                output, _ = await process.communicate()
                text = output.decode(errors="replace")[-12000:]
                operation.progress.extend(line[:300] for line in text.splitlines()[-40:])
                if process.returncode != 0 or "Programming Finished" not in text:
                    raise FirmwareValidationError(f"uploader failed with exit code {process.returncode}")
                operation.state = "reenumerating"
                operation.progress.append("programming_verified")
                for _ in range(30):
                    await asyncio.sleep(0.5)
                    if any(row["hardware_serial"] == operation.hardware_serial for row in self.boards()):
                        operation.state = "complete"
                        operation.progress.append("same_device_reenumerated")
                        return
                raise FirmwareValidationError("the same hardware serial did not re-enumerate")
            except Exception as exc:
                operation.state = "failed"
                operation.error = str(exc)[:500]

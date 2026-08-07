#!/usr/bin/env python3
import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_release(artifact: Path, output: Path) -> Path:
    version = (PACKAGE / "VERSION").read_text(encoding="utf-8").strip()
    protocol = (ROOT / "shared_protocol" / "VERSION").read_text(encoding="utf-8").strip()
    release = output / f"sensor-package-{version}"
    if release.exists():
        raise FileExistsError(f"release directory already exists: {release}")
    release.mkdir(parents=True)
    target = release / artifact.name
    shutil.copy2(artifact, target)
    try:
        commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    manifest = {"schema_version": 1, "sensor_package_version": version, "firmware_version": version,
                "protocol_version": protocol, "configuration_schema_version": 1, "git_commit": commit,
                "build_status": "compiled-unverified", "board_fqbn": "SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs",
                "required_core": "SiliconLabs:silabs", "required_libraries": ["LSM6DS3"],
                "artifact_filename": target.name, "artifact_sha256": sha256(target),
                "created_at_utc": datetime.now(UTC).isoformat()}
    (release / "firmware-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (release / "SHA256SUMS").write_text(f"{manifest['artifact_sha256']}  {target.name}\n", encoding="utf-8")
    shutil.copy2(PACKAGE / "CHANGELOG.md", release / "RELEASE_NOTES.md")
    return release


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--output", type=Path, default=PACKAGE / "dist")
    args = parser.parse_args()
    if not args.artifact.is_file():
        parser.error("compiled artifact does not exist")
    print(create_release(args.artifact.resolve(), args.output.resolve()))

import importlib.util
import json
from pathlib import Path


def load_module():
    path = Path(__file__).parents[1] / "scripts" / "package_release.py"
    spec = importlib.util.spec_from_file_location("package_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_manifest_and_checksum(tmp_path):
    module = load_module()
    artifact = tmp_path / "firmware.bin"
    artifact.write_bytes(b"firmware")
    release = module.create_release(artifact, tmp_path / "dist")
    manifest = json.loads((release / "firmware-manifest.json").read_text())
    assert manifest["artifact_sha256"] == module.sha256(release / "firmware.bin")
    assert manifest["protocol_version"] == "1.1.0"


def test_component_versions_are_semantic():
    from packaging.version import Version
    root = Path(__file__).parents[2]
    assert Version((root / "sensor_package/VERSION").read_text().strip())
    assert Version((root / "gateway/VERSION").read_text().strip())
    assert Version((root / "shared_protocol/VERSION").read_text().strip())

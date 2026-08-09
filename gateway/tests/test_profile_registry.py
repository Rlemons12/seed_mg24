import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from gateway.app.profiles.registry import DuplicateProfileError, ProfileRegistry

BUNDLED = Path(__file__).parents[2] / "sensor_package" / "profiles" / "built_in"


def profile_data():
    return json.loads((BUNDLED / "generic-analog-raw-1.0.0.json").read_text())


def test_loads_valid_profiles_and_filters(tmp_path):
    registry = ProfileRegistry(tmp_path, BUNDLED)
    registry.reload()
    assert registry.get("generic.analog_raw", "1.0.0").conversion.type == "unconfigured"
    assert all(profile.interface.type == "analog" for profile in registry.list(interface_type="analog"))
    assert not registry.errors


def test_unknown_fields_and_unsafe_conversion_rejected():
    value = profile_data()
    value["unexpected"] = True
    with pytest.raises(ValidationError):
        ProfileRegistry.parse(json.dumps(value))
    value = profile_data()
    value["conversion"] = {"type": "linear", "gain": None, "offset": None, "coefficients": None, "points": None}
    with pytest.raises(ValidationError):
        ProfileRegistry.parse(json.dumps(value))


def test_thresholds_disabled_and_verified_requires_provenance():
    profile = ProfileRegistry.parse(json.dumps(profile_data()))
    assert not profile.alarms.defaults_enabled
    value = profile_data()
    value["status"] = "verified"
    value["provenance"]["verified"] = True
    with pytest.raises(ValidationError):
        ProfileRegistry.parse(json.dumps(value))


def test_duplicate_and_import_limits(tmp_path):
    (tmp_path / "duplicate.json").write_text((BUNDLED / "generic-analog-raw-1.0.0.json").read_text())
    registry = ProfileRegistry(tmp_path, BUNDLED, max_upload_bytes=4096)
    registry.reload()
    assert any("duplicate" in error.error for error in registry.errors)
    with pytest.raises(ValueError):
        registry.import_profile(b"x" * 4097)
    with pytest.raises(DuplicateProfileError):
        registry.import_profile((BUNDLED / "generic-analog-raw-1.0.0.json").read_bytes())

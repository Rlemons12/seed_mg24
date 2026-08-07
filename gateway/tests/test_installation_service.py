import json
from pathlib import Path

import pytest

from gateway.app.database import create_database_engine, create_session_factory, initialize_database
from gateway.app.profiles.registry import ProfileRegistry
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.repositories.installation_repository import InstallationRepository
from gateway.app.schemas import InstallationConfiguration, InstallationCreate, InstallationUpdate
from gateway.app.services.installation_service import InstallationValidationError, SensorInstallationService

BUNDLED = Path(__file__).parents[2] / "sensor_package" / "profiles" / "built_in"


def setup(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    factory = create_session_factory(engine)
    profiles = ProfileRegistry(settings.sensor_profile_directory, BUNDLED)
    profiles.reload()
    return factory, profiles


def request(device_id="ARM2001-01", interface="D0"):
    return InstallationCreate(
        node_id="MG24-0001",
        device_id=device_id,
        display_name="West wall",
        sensor_profile_id="generic.analog_raw",
        sensor_profile_version="1.0.0",
        interface_id=interface,
        configuration=InstallationConfiguration(),
    )


def test_node_supports_multiple_sensors_and_editable_name(settings):
    factory, profiles = setup(settings)
    with factory() as session:
        DeviceRepository(session).create(device_id="MG24-0001", display_name="Node")
        service = SensorInstallationService(
            InstallationRepository(session), DeviceRepository(session), profiles, settings.device_id_pattern
        )
        first = service.create_draft(request())
        second = service.create_draft(request("ARM2001-02", "D1"))
        service.update(first, InstallationUpdate(display_name="Renamed"))
        assert first.display_name == "Renamed" and first.device_id == "ARM2001-01" and second.node_id == first.node_id


def test_rejects_mismatch_and_occupied_exclusive_interface(settings):
    factory, profiles = setup(settings)
    with factory() as session:
        DeviceRepository(session).create(device_id="MG24-0001", display_name="Node")
        repository = InstallationRepository(session)
        service = SensorInstallationService(repository, DeviceRepository(session), profiles, settings.device_id_pattern)
        with pytest.raises(InstallationValidationError):
            service.create_draft(request(interface="MIC"))
        first = service.create_draft(request())
        repository.update(first, enabled=True, provisioning_state="active")
        with pytest.raises(InstallationValidationError):
            service.create_draft(request("ARM2001-02"))


def test_generic_raw_remains_uncalibrated(settings):
    factory, profiles = setup(settings)
    with factory() as session:
        DeviceRepository(session).create(device_id="MG24-0001", display_name="Node")
        service = SensorInstallationService(
            InstallationRepository(session), DeviceRepository(session), profiles, settings.device_id_pattern
        )
        item = service.create_draft(request())
        assert item.calibration_status == "not_configured"
        assert json.loads(item.configuration_json)["calibration_enabled"] is False

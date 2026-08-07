import pytest

from gateway.app.database import create_database_engine, create_session_factory, initialize_database
from gateway.app.repositories.device_repository import DeviceRepository, DuplicateDeviceError


def test_device_repository_enforces_unique_id(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        repository = DeviceRepository(session)
        repository.create(device_id="ARM2001-01", display_name="First")
        with pytest.raises(DuplicateDeviceError):
            repository.create(device_id="ARM2001-01", display_name="Second")


def test_display_name_updates_without_device_id(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        repository = DeviceRepository(session)
        device = repository.create(device_id="ARM2001-01", display_name="Old")
        repository.update(device, display_name="West wall")
        assert repository.get("ARM2001-01").display_name == "West wall"
        with pytest.raises(ValueError):
            repository.update(device, device_id="ARM2001-02")

from datetime import UTC, datetime

import pytest

from gateway.app.database import create_database_engine, create_session_factory, initialize_database
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.schemas import DeviceCreate, DeviceUpdate, Discovery
from gateway.app.services.device_service import DeviceService, DeviceValidationError


def discovery():
    return Discovery(address="AA", name="node", compatible=True, compatibility_reason="service", last_seen_at=datetime.now(UTC))


def test_registration_and_editable_name(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        service = DeviceService(DeviceRepository(session), settings.device_id_pattern)
        device = service.register(DeviceCreate(device_id="ARM2001-01", display_name="Boiler room", discovery_address="AA"), discovery())
        service.update(device, DeviceUpdate(display_name="West wall"))
        assert device.device_id == "ARM2001-01" and device.display_name == "West wall"


def test_invalid_identifier_rejected(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        service = DeviceService(DeviceRepository(session), settings.device_id_pattern)
        with pytest.raises(DeviceValidationError):
            service.register(DeviceCreate(device_id="bad id", display_name="Good", discovery_address="AA"), discovery())


def test_blank_display_name_rejected_by_schema():
    with pytest.raises(ValueError):
        DeviceCreate(device_id="ARM2001-01", display_name="   ", discovery_address="AA")

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gateway.app.database import create_database_engine, create_session_factory, initialize_database
from gateway.app.models import Reading
from gateway.app.profiles.registry import ProfileRegistry
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.repositories.installation_repository import InstallationRepository
from gateway.app.schemas import InstallationConfiguration, InstallationCreate
from gateway.app.services.installation_service import SensorInstallationService
from gateway.app.services.provisioning_service import ProvisioningError, SensorProvisioningService

BUNDLED = Path(__file__).parents[2] / "sensor_package" / "profiles" / "built_in"


class FakeConfigurator:
    def __init__(self, fail=False, mismatch=False):
        self.fail, self.mismatch, self.calls, self.value = fail, mismatch, 0, {}

    async def apply(self, _node, _interface, _transaction, configuration):
        self.calls += 1
        if self.fail:
            raise TimeoutError
        self.value = dict(configuration)
        return self.value

    async def read_back(self, *_):
        return {**self.value, "report_interval_ms": 999} if self.mismatch else self.value


class SlowConfigurator(FakeConfigurator):
    async def apply(self, *args):
        await asyncio.sleep(0.1)
        return await super().apply(*args)


def setup(settings):
    engine = create_database_engine(settings)
    initialize_database(engine)
    factory = create_session_factory(engine)
    profiles = ProfileRegistry(settings.sensor_profile_directory, BUNDLED)
    profiles.reload()
    with factory() as session:
        node = DeviceRepository(session).create(
            device_id="MG24-0001", display_name="Node", compatibility_status="compatible"
        )
        service = SensorInstallationService(
            InstallationRepository(session), DeviceRepository(session), profiles, settings.device_id_pattern
        )
        item = service.create_draft(
            InstallationCreate(
                node_id=node.device_id,
                device_id="ARM2001-01",
                display_name="Sensor",
                sensor_profile_id="generic.analog_raw",
                sensor_profile_version="1.0.0",
                interface_id="D0",
                configuration=InstallationConfiguration(),
            )
        )
        session.add(
            Reading(
                registered_device_id=node.id,
                received_at=datetime.now(UTC),
                device_uptime_ms=1,
                measured_at_device_uptime=1,
                sequence_number=1,
                session_id="boot",
                record_type="measurement",
                channel="analog_0",
                raw_value=100,
                normalized_value=100,
                unit="adc_count",
                quality="uncalibrated",
                payload_json="{}",
                delayed=False,
            )
        )
        session.commit()
        installation_id = item.installation_id
    return factory, profiles, installation_id


@pytest.mark.asyncio
async def test_successful_apply_verify_and_idempotent_retry(settings):
    factory, profiles, installation_id = setup(settings)
    adapter = FakeConfigurator()
    service = SensorProvisioningService(factory, profiles, adapter)
    await service.validate(installation_id)
    active = await service.apply(installation_id)
    again = await service.apply(installation_id)
    assert active.provisioning_state == "active" and active.enabled and again.installation_id == installation_id
    assert adapter.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter,error", [(FakeConfigurator(fail=True), "acknowledgement"), (FakeConfigurator(mismatch=True), "read-back")]
)
async def test_failed_acknowledgement_and_readback(settings, adapter, error):
    factory, profiles, installation_id = setup(settings)
    service = SensorProvisioningService(factory, profiles, adapter)
    await service.validate(installation_id)
    with pytest.raises(ProvisioningError, match=error):
        await service.apply(installation_id)
    with factory() as session:
        item = InstallationRepository(session).get(installation_id)
        assert item.provisioning_state == "failed" and not item.enabled


@pytest.mark.asyncio
async def test_concurrent_apply_is_locked(settings):
    factory, profiles, installation_id = setup(settings)
    adapter = FakeConfigurator()
    service = SensorProvisioningService(factory, profiles, adapter)
    await service.validate(installation_id)
    results = await asyncio.gather(service.apply(installation_id), service.apply(installation_id))
    assert all(item.installation_id == installation_id for item in results)
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_timeout_is_failed_and_retryable(settings):
    factory, profiles, installation_id = setup(settings)
    service = SensorProvisioningService(factory, profiles, SlowConfigurator(), timeout_seconds=0.01)
    await service.validate(installation_id)
    with pytest.raises(ProvisioningError, match="acknowledgement"):
        await service.apply(installation_id)
    with factory() as session:
        assert InstallationRepository(session).get(installation_id).provisioning_state == "failed"


@pytest.mark.asyncio
async def test_failed_replacement_restores_previous_active_configuration(settings):
    factory, profiles, installation_id = setup(settings)
    good = SensorProvisioningService(factory, profiles, FakeConfigurator())
    await good.validate(installation_id)
    await good.apply(installation_id)
    with factory() as session:
        repository = InstallationRepository(session)
        item = repository.get(installation_id)
        old = item.configuration_json
        changed = old.replace('"report_interval_ms":100', '"report_interval_ms":200')
        repository.update(
            item, previous_configuration_json=old, configuration_json=changed, provisioning_state="ready_to_apply", enabled=False
        )
    failing = SensorProvisioningService(factory, profiles, FakeConfigurator(fail=True))
    with pytest.raises(ProvisioningError):
        await failing.apply(installation_id)
    with factory() as session:
        restored = InstallationRepository(session).get(installation_id)
        assert restored.provisioning_state == "active" and restored.enabled and restored.configuration_json == old

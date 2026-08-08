import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from gateway.app.models import Reading
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.repositories.installation_repository import InstallationRepository
from gateway.app.services.channel_configuration_service import DefaultChannelConfigurationService
from gateway.app.services.installation_service import SensorInstallationService
from gateway.app.services.node_capability_service import NodeCapabilityService


class ProvisioningError(ValueError):
    pass


class PiAuthoritativeConfigurator:
    """Current firmware adapter: safe configuration remains authoritative on the Pi."""

    def __init__(self) -> None:
        self._applied: dict[tuple[str, str], dict] = {}

    async def apply(self, node_id: str, interface_id: str, transaction_id: str, configuration: dict) -> dict:
        self._applied[(node_id, interface_id)] = dict(configuration)
        return dict(configuration)

    async def read_back(self, node_id: str, interface_id: str, transaction_id: str) -> dict:
        return dict(self._applied.get((node_id, interface_id), {}))


class BlePersistentConfigurator:
    """Production adapter: acknowledgement and readback come from the MG24 itself."""

    def __init__(self, session_factory, provisioner, manager_provider=None) -> None:
        self.session_factory = session_factory
        self.provisioner = provisioner
        self.manager_provider = manager_provider
        self._verified: dict[str, dict] = {}

    async def apply(self, node_id: str, interface_id: str, transaction_id: str, configuration: dict) -> dict:
        if interface_id != "MIC":
            raise ValueError("firmware persistence currently supports the built-in microphone interface")
        with self.session_factory() as session:
            device = DeviceRepository(session).get(node_id)
            if device is None or not device.ble_address:
                raise ConnectionError("node BLE address is unavailable")
            address = device.ble_address
        manager = self.manager_provider() if self.manager_provider else None
        if manager:
            await manager.remove(node_id)
            await asyncio.sleep(0.5)
        try:
            result = await self.provisioner.provision(address, node_id, transaction_id[:16], configuration)
        finally:
            if manager:
                manager.schedule(node_id, address)
        _ = result["readback"]
        normalized = dict(configuration)
        self._verified[transaction_id] = normalized
        return normalized

    async def read_back(self, node_id: str, interface_id: str, transaction_id: str) -> dict:
        if transaction_id not in self._verified:
            raise ValueError("no verified device acknowledgement exists for this transaction")
        return dict(self._verified[transaction_id])


class SensorProvisioningService:
    def __init__(self, session_factory: sessionmaker[Session], profiles, configurator=None, timeout_seconds: float = 10.0) -> None:
        self.session_factory = session_factory
        self.profiles = profiles
        self.configurator = configurator or PiAuthoritativeConfigurator()
        self.timeout_seconds = timeout_seconds
        self._node_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._interface_locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    async def validate(self, installation_id: str):
        with self.session_factory() as session:
            repository = InstallationRepository(session)
            installation = repository.get(installation_id)
            if installation is None or installation.archived:
                raise ProvisioningError("installation not found")
            repository.update(installation, provisioning_state="validating", provisioning_error=None)
            try:
                configuration = SensorInstallationService.configuration(installation)
                profile = self.profiles.get(installation.sensor_profile_id, installation.sensor_profile_version)
                if profile is None:
                    raise ProvisioningError("pinned sensor profile is unavailable")
                capabilities = NodeCapabilityService(DeviceRepository(session)).get(installation.node_id)
                DefaultChannelConfigurationService().validate(profile, capabilities, installation.interface_id, configuration)
            except (ValueError, ProvisioningError) as exc:
                repository.update(
                    installation,
                    provisioning_state="failed",
                    verification_status="failed",
                    provisioning_error=str(exc)[:500],
                    enabled=False,
                )
                raise ProvisioningError(str(exc)) from exc
            return repository.update(installation, provisioning_state="ready_to_apply", provisioning_error=None)

    async def apply(self, installation_id: str):
        with self.session_factory() as session:
            repository = InstallationRepository(session)
            installation = repository.get(installation_id)
            if installation is None or installation.archived:
                raise ProvisioningError("installation not found")
            if installation.provisioning_state == "active":
                return installation
            node_id, interface_id = installation.node_id, installation.interface_id
        async with self._node_locks[node_id], self._interface_locks[(node_id, interface_id)]:
            with self.session_factory() as session:
                repository = InstallationRepository(session)
                installation = repository.get(installation_id)
                devices = DeviceRepository(session)
                node = devices.get(node_id)
                if node is None or node.compatibility_status != "compatible":
                    status = node.compatibility_status if node else "unknown"
                    raise ProvisioningError(f"node firmware compatibility is {status}; configuration is blocked")
                capability = NodeCapabilityService(devices).get(node_id)
                selected_interface = next(item for item in capability.interfaces if item.interface_id == interface_id)
                occupied = repository.occupied_interface(node_id, interface_id, exclude_installation_id=installation_id)
                if selected_interface.exclusive and occupied:
                    raise ProvisioningError(f"exclusive interface is active for {occupied.device_id}")
                if installation.active_transaction_id:
                    existing = repository.get_attempt(installation.active_transaction_id)
                    if existing and existing.state in {"applying", "verifying", "active"}:
                        return installation
                if installation.provisioning_state not in {"ready_to_apply", "failed", "draft"}:
                    raise ProvisioningError(f"installation cannot be applied from {installation.provisioning_state}")
                transaction_id = uuid4().hex
                requested = json.loads(installation.configuration_json)
                repository.add_attempt(
                    transaction_id=transaction_id,
                    installation_id=installation_id,
                    requested_configuration_json=json.dumps(requested, separators=(",", ":")),
                    state="applying",
                )
                repository.update(
                    installation,
                    provisioning_state="applying",
                    active_transaction_id=transaction_id,
                    provisioning_error=None,
                    enabled=False,
                )
            try:
                applied = await asyncio.wait_for(
                    self.configurator.apply(node_id, interface_id, transaction_id, requested), timeout=self.timeout_seconds
                )
            except (TimeoutError, ConnectionError, ValueError) as exc:
                await self._fail(installation_id, transaction_id, type(exc).__name__)
                raise ProvisioningError("configuration acknowledgement failed") from exc
            with self.session_factory() as session:
                repository = InstallationRepository(session)
                attempt = repository.get_attempt(transaction_id)
                repository.update_attempt(attempt, state="verifying", applied_configuration_json=json.dumps(applied, separators=(",", ":")))
                installation = repository.get(installation_id)
                repository.update(installation, provisioning_state="verifying")
            return await self.verify(installation_id)

    async def verify(self, installation_id: str):
        with self.session_factory() as session:
            repository = InstallationRepository(session)
            installation = repository.get(installation_id)
            if installation is None or not installation.active_transaction_id:
                raise ProvisioningError("installation has no transaction to verify")
            transaction_id = installation.active_transaction_id
            requested = json.loads(installation.configuration_json)
            node_id, interface_id = installation.node_id, installation.interface_id
        try:
            read_back = await asyncio.wait_for(
                self.configurator.read_back(node_id, interface_id, transaction_id), timeout=self.timeout_seconds
            )
        except (TimeoutError, ConnectionError, ValueError) as exc:
            await self._fail(installation_id, transaction_id, type(exc).__name__)
            raise ProvisioningError("configuration read-back failed") from exc
        if read_back != requested:
            await self._fail(installation_id, transaction_id, "configuration_readback_mismatch")
            raise ProvisioningError("configuration read-back does not match the request")
        with self.session_factory() as session:
            repository = InstallationRepository(session)
            installation = repository.get(installation_id)
            device = DeviceRepository(session).get(installation.node_id)
            capabilities = NodeCapabilityService(DeviceRepository(session)).get(installation.node_id)
            interface = next(item for item in capabilities.interfaces if item.interface_id == installation.interface_id)
            cutoff = datetime.now(UTC) - timedelta(seconds=60)
            reading = session.scalar(
                select(Reading)
                .where(
                    Reading.registered_device_id == device.id,
                    Reading.channel.in_(interface.telemetry_channels),
                    Reading.received_at >= cutoff,
                    Reading.quality.not_in(("invalid", "sensor_fault", "stale")),
                )
                .order_by(Reading.received_at.desc())
            )
            if reading is None:
                await self._fail(installation_id, transaction_id, "no_recent_valid_telemetry")
                raise ProvisioningError("no recent valid telemetry was received for the selected interface")
            attempt = repository.get_attempt(transaction_id)
            repository.update_attempt(attempt, state="active")
            return repository.update(
                installation,
                previous_configuration_json=installation.configuration_json,
                provisioning_state="active",
                verification_status="verified",
                enabled=True,
                provisioning_error=None,
                active_transaction_id=None,
                last_seen_at=reading.received_at,
                last_valid_reading_at=reading.received_at,
            )

    async def _fail(self, installation_id: str, transaction_id: str, error: str) -> None:
        with self.session_factory() as session:
            repository = InstallationRepository(session)
            attempt = repository.get_attempt(transaction_id)
            if attempt:
                repository.update_attempt(attempt, state="failed", error=error)
            installation = repository.get(installation_id)
            if installation:
                if installation.previous_configuration_json:
                    repository.update(
                        installation,
                        configuration_json=installation.previous_configuration_json,
                        previous_configuration_json=None,
                        provisioning_state="active",
                        verification_status="verified",
                        provisioning_error=f"replacement_failed:{error}",
                        active_transaction_id=None,
                        enabled=True,
                    )
                else:
                    repository.update(
                        installation,
                        provisioning_state="failed",
                        verification_status="failed",
                        provisioning_error=error,
                        active_transaction_id=None,
                        enabled=False,
                    )

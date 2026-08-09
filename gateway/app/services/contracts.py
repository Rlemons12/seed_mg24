from typing import Protocol

from gateway.app.profiles.models import SensorProfile
from gateway.app.schemas import InstallationConfiguration, NodeCapabilities


class SensorProfileRegistry(Protocol):
    def get(self, profile_id: str, version: str | None = None) -> SensorProfile | None: ...


class NodeCapabilityServiceProtocol(Protocol):
    def get(self, node_id: str) -> NodeCapabilities: ...


class ChannelConfigurationService(Protocol):
    def validate(
        self, profile: SensorProfile, capabilities: NodeCapabilities, interface_id: str, configuration: InstallationConfiguration
    ) -> None: ...


class RawMeasurementDecoder(Protocol):
    def decode(self, payload: bytes) -> dict[str, float]: ...


class MeasurementConverter(Protocol):
    def convert(self, value: float, profile: SensorProfile) -> float | None: ...


class MeasurementValidator(Protocol):
    def validate(self, value: float, profile: SensorProfile) -> bool: ...


class NodeConfigurator(Protocol):
    async def apply(self, node_id: str, interface_id: str, transaction_id: str, configuration: dict) -> dict: ...
    async def read_back(self, node_id: str, interface_id: str, transaction_id: str) -> dict: ...


class SensorProvisioningServiceProtocol(Protocol):
    async def apply(self, installation_id: str): ...
    async def verify(self, installation_id: str): ...

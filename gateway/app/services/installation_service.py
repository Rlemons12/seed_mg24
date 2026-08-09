import json
import re
from uuid import uuid4

from gateway.app.models import SensorInstallation
from gateway.app.profiles.registry import ProfileRegistry
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.repositories.installation_repository import InstallationRepository
from gateway.app.schemas import InstallationConfiguration, InstallationCreate, InstallationUpdate
from gateway.app.services.channel_configuration_service import ConfigurationValidationError, DefaultChannelConfigurationService
from gateway.app.services.node_capability_service import NodeCapabilityService


class InstallationValidationError(ValueError):
    pass


class SensorInstallationService:
    def __init__(
        self, installations: InstallationRepository, devices: DeviceRepository, profiles: ProfileRegistry, device_id_pattern: str
    ) -> None:
        self.installations = installations
        self.devices = devices
        self.profiles = profiles
        self.pattern = re.compile(device_id_pattern)
        self.configuration_service = DefaultChannelConfigurationService()

    def create_draft(self, request: InstallationCreate) -> SensorInstallation:
        if not self.pattern.fullmatch(request.device_id):
            raise InstallationValidationError("device_id does not match the configured equipment identifier pattern")
        if self.installations.get_by_device_id(request.device_id):
            raise InstallationValidationError("device_id is already assigned to an attached sensor")
        if self.devices.get(request.device_id):
            raise InstallationValidationError("device_id conflicts with an existing MG24 node identity")
        profile = self.profiles.get(request.sensor_profile_id, request.sensor_profile_version)
        if profile is None:
            raise InstallationValidationError("sensor profile ID and version were not found")
        capabilities = NodeCapabilityService(self.devices).get(request.node_id)
        try:
            self.configuration_service.validate(profile, capabilities, request.interface_id, request.configuration)
        except ConfigurationValidationError as exc:
            raise InstallationValidationError(str(exc)) from exc
        interface = next(item for item in capabilities.interfaces if item.interface_id == request.interface_id)
        occupied = self.installations.occupied_interface(request.node_id, request.interface_id)
        if interface.exclusive and occupied:
            raise InstallationValidationError(f"interface is already assigned to {occupied.device_id}")
        calibration_status = "not_configured" if profile.conversion.type == "unconfigured" else "profile_configured"
        return self.installations.create(
            installation_id=f"inst-{uuid4().hex}",
            node_id=request.node_id,
            device_id=request.device_id,
            display_name=request.display_name,
            sensor_profile_id=profile.profile_id,
            sensor_profile_version=profile.profile_version,
            interface_id=request.interface_id,
            location=request.location,
            description=request.description,
            configuration_json=request.configuration.model_dump_json(),
            calibration_status=calibration_status,
            verification_status="pending",
            provisioning_state="draft",
            enabled=False,
        )

    def update(self, installation: SensorInstallation, request: InstallationUpdate) -> SensorInstallation:
        values = request.model_dump(exclude_unset=True, exclude={"configuration"})
        if request.configuration is not None:
            profile = self.profiles.get(installation.sensor_profile_id, installation.sensor_profile_version)
            capabilities = NodeCapabilityService(self.devices).get(installation.node_id)
            self.configuration_service.validate(profile, capabilities, installation.interface_id, request.configuration)
            values["configuration_json"] = request.configuration.model_dump_json()
            if installation.provisioning_state == "active":
                values.update(
                    previous_configuration_json=installation.configuration_json,
                    provisioning_state="ready_to_apply",
                    verification_status="pending",
                    enabled=False,
                )
        return self.installations.update(installation, **values)

    @staticmethod
    def configuration(installation: SensorInstallation) -> InstallationConfiguration:
        return InstallationConfiguration.model_validate(json.loads(installation.configuration_json))

from gateway.app.profiles.models import ProfileStatus, SensorProfile
from gateway.app.schemas import InstallationConfiguration, NodeCapabilities
from gateway.app.services.node_capability_service import NodeCapabilityService


class ConfigurationValidationError(ValueError):
    pass


class DefaultChannelConfigurationService:
    def validate(
        self, profile: SensorProfile, capabilities: NodeCapabilities, interface_id: str, configuration: InstallationConfiguration
    ) -> None:
        interface = next((item for item in capabilities.interfaces if item.interface_id == interface_id), None)
        if interface is None:
            raise ConfigurationValidationError("interface is not reported by the selected MG24 node")
        if profile.status in {ProfileStatus.DISABLED, ProfileStatus.DEPRECATED}:
            raise ConfigurationValidationError(f"profile status {profile.status} cannot be used for a new installation")
        if profile.interface.type != interface.type:
            raise ConfigurationValidationError("sensor profile is incompatible with the selected interface type")
        missing = set(profile.firmware.required_capabilities) - set(interface.capabilities)
        if missing:
            raise ConfigurationValidationError(f"interface lacks required capabilities: {', '.join(sorted(missing))}")
        if not NodeCapabilityService.firmware_satisfies(capabilities.firmware_version, profile.firmware.minimum_version):
            raise ConfigurationValidationError("node firmware does not satisfy the profile minimum version")
        sampling = profile.sampling
        for value in (configuration.sample_interval_ms, configuration.processing_interval_ms, configuration.report_interval_ms):
            if sampling.minimum_interval_ms is not None and value < sampling.minimum_interval_ms:
                raise ConfigurationValidationError("configured interval is below the profile minimum")
            if sampling.maximum_interval_ms is not None and value > sampling.maximum_interval_ms:
                raise ConfigurationValidationError("configured interval exceeds the profile maximum")
        if configuration.filter_type not in profile.filter.supported:
            raise ConfigurationValidationError("selected filter is not supported by the profile")
        if configuration.filter_type not in capabilities.filters:
            raise ConfigurationValidationError("selected filter is not reported by the node")
        if configuration.filter_window > profile.filter.maximum_window:
            raise ConfigurationValidationError("filter window exceeds the profile maximum")
        if configuration.calibration_enabled:
            raise ConfigurationValidationError("user calibration is not enabled by this profile")
        if any(value is not None for value in (configuration.calibration_gain, configuration.calibration_offset)):
            raise ConfigurationValidationError("calibration parameters require an explicitly calibration-enabled profile")
        thresholds = (configuration.warning_low, configuration.warning_high, configuration.alarm_low, configuration.alarm_high)
        if configuration.alarms_enabled and (not profile.alarms.supported or all(value is None for value in thresholds)):
            raise ConfigurationValidationError("alarms require profile support and explicitly confirmed threshold values")
        if not configuration.alarms_enabled and any(value is not None for value in thresholds):
            raise ConfigurationValidationError("threshold values require alarms_enabled=true")
        if not interface.configuration_supported:
            established = (
                configuration.sample_interval_ms,
                configuration.processing_interval_ms,
                configuration.report_interval_ms,
                configuration.heartbeat_interval_ms,
            )
            if established != (100, 100, 100, 30000) or configuration.filter_type != profile.filter.default:
                raise ConfigurationValidationError(
                    "this interface does not support runtime reconfiguration; use its established profile defaults"
                )

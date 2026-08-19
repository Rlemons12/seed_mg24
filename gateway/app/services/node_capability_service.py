from packaging.version import Version

from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.schemas import NodeCapabilities, NodeInterface


class NodeNotFoundError(ValueError):
    pass


class NodeCapabilityService:
    """Truthful capability view for the firmware currently in this repository."""

    def __init__(self, repository: DeviceRepository) -> None:
        self.repository = repository

    def get(self, node_id: str, reported: dict | None = None) -> NodeCapabilities:
        node = self.repository.get(node_id)
        if node is None or node.archived:
            raise NodeNotFoundError("MG24 node not found")
        interfaces = [
            NodeInterface(
                interface_id="MIC",
                type="built_in",
                capabilities=["built_in_microphone"],
                telemetry_channels=["microphone_raw", "microphone_percent"],
                exclusive=True,
                configuration_supported=True,
                configuration_persistence="persistent",
            ),
            NodeInterface(
                interface_id="IMU0",
                type="built_in",
                capabilities=["built_in_imu_accelerometer", "built_in_imu_gyroscope"],
                telemetry_channels=[
                    "acceleration_x",
                    "acceleration_y",
                    "acceleration_z",
                    "angular_velocity_x",
                    "angular_velocity_y",
                    "angular_velocity_z",
                ],
                exclusive=False,
            ),
            NodeInterface(
                interface_id="VBAT",
                type="built_in",
                capabilities=["built_in_battery"],
                telemetry_channels=["battery_voltage"],
                exclusive=True,
            ),
        ]
        interfaces.extend(
            NodeInterface(
                interface_id=f"D{index}", type="analog", capabilities=["raw_adc"], telemetry_channels=[f"analog_{index}"], exclusive=True
            )
            for index in range(6)
        )
        result = NodeCapabilities(
            node_id=node.device_id,
            firmware_version=node.firmware_version,
            interfaces=interfaces,
            filters=["none", "ema", "moving_average", "median", "digital_debounce"],
            reporting_modes=["live", "edge_summary", "event", "heartbeat"],
            configuration_readback=False,
        )
        if reported:
            reported_interfaces = {item.get("interface_id"): item for item in reported.get("interfaces", []) if isinstance(item, dict)}
            result.interfaces = [
                item
                for item in result.interfaces
                if item.interface_id in reported_interfaces
                and reported_interfaces[item.interface_id].get("type") == item.type
                and set(item.capabilities).issubset(set(reported_interfaces[item.interface_id].get("capabilities", [])))
            ]
            processing = reported.get("processing", {})
            if isinstance(processing, dict):
                result.filters = [item for item in result.filters if item in processing.get("filters", [])]
                result.reporting_modes = [item for item in result.reporting_modes if item in processing.get("reporting_modes", [])]
            result.firmware_version = reported.get("firmware_version") or result.firmware_version
        return result

    @staticmethod
    def firmware_satisfies(current: str | None, minimum: str | None) -> bool:
        return minimum is None or current is not None and Version(current) >= Version(minimum)

import re

from gateway.app.repositories.device_repository import DeviceRepository, DuplicateDeviceError
from gateway.app.schemas import DeviceCreate, DeviceUpdate, Discovery


class DeviceValidationError(ValueError):
    pass


class DeviceService:
    def __init__(self, repository: DeviceRepository, device_id_pattern: str) -> None:
        self.repository = repository
        self.pattern = re.compile(device_id_pattern)

    def register(self, request: DeviceCreate, discovery: Discovery):
        if not self.pattern.fullmatch(request.device_id):
            raise DeviceValidationError("device_id does not match the configured equipment identifier pattern")
        if request.discovery_address != discovery.address:
            raise DeviceValidationError("selected discovery no longer matches the request")
        if not discovery.compatible and not request.allow_incompatible:
            raise DeviceValidationError("device is not confirmed compatible; explicit override is required")
        existing_address = self.repository.get_by_ble_address(discovery.address)
        if existing_address is not None:
            raise DuplicateDeviceError(f"BLE address is already associated with {existing_address.device_id}")
        if discovery.stable_device_id:
            existing_identity = self.repository.get(discovery.stable_device_id)
            if existing_identity is not None:
                raise DuplicateDeviceError("firmware identity is already registered")
            if discovery.stable_device_id != request.device_id:
                raise DeviceValidationError("entered device_id does not match the stable firmware identity")
        return self.repository.create(
            device_id=request.device_id,
            display_name=request.display_name,
            device_type=request.device_type,
            ble_address=discovery.address,
            ble_advertised_name=discovery.name,
            location=request.location,
            description=request.description,
            enabled=True,
        )

    def update(self, device, request: DeviceUpdate):
        return self.repository.update(device, **request.model_dump(exclude_unset=True))

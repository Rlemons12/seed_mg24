from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from gateway.app.models import RegisteredDevice, utc_now


class DuplicateDeviceError(ValueError):
    pass


class DeviceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, include_archived: bool = False) -> list[RegisteredDevice]:
        statement = select(RegisteredDevice).order_by(RegisteredDevice.device_id)
        if not include_archived:
            statement = statement.where(RegisteredDevice.archived.is_(False))
        return list(self.session.scalars(statement))

    def get(self, device_id: str) -> RegisteredDevice | None:
        return self.session.scalar(select(RegisteredDevice).where(RegisteredDevice.device_id == device_id))

    def get_by_ble_address(self, address: str) -> RegisteredDevice | None:
        return self.session.scalar(
            select(RegisteredDevice).where(RegisteredDevice.ble_address == address, RegisteredDevice.archived.is_(False))
        )

    def get_by_hardware_id(self, hardware_id: str) -> RegisteredDevice | None:
        return self.session.scalar(select(RegisteredDevice).where(RegisteredDevice.hardware_id == hardware_id))

    def get_other_by_hardware_id(self, hardware_id: str, record_id: int) -> RegisteredDevice | None:
        return self.session.scalar(
            select(RegisteredDevice).where(
                RegisteredDevice.hardware_id == hardware_id, RegisteredDevice.id != record_id
            )
        )

    def create(self, **values) -> RegisteredDevice:
        device = RegisteredDevice(**values)
        self.session.add(device)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateDeviceError("device_id is already registered") from exc
        self.session.refresh(device)
        return device

    def update(self, device: RegisteredDevice, **values) -> RegisteredDevice:
        if "device_id" in values and values["device_id"] != device.device_id:
            raise ValueError("device_id cannot be changed")
        for key, value in values.items():
            setattr(device, key, value)
        device.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(device)
        return device

    def update_runtime(
        self,
        device: RegisteredDevice,
        *,
        status: str | None = None,
        last_seen_at: datetime | None = None,
        last_connected_at: datetime | None = None,
        ble_address: str | None = None,
    ) -> None:
        if status is not None:
            device.connection_status = status
        if last_seen_at is not None:
            device.last_seen_at = last_seen_at
        if last_connected_at is not None:
            device.last_connected_at = last_connected_at
        if ble_address is not None:
            device.ble_address = ble_address
        device.updated_at = utc_now()
        self.session.commit()

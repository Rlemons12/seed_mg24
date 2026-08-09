import json
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.orm import Session

from gateway.app.models import DeviceLifecycleEvent, SensorInstallation, utc_now
from gateway.app.repositories.device_repository import DeviceRepository


class LifecycleError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class LifecycleResult:
    operation_id: str
    status: str
    device_id: str
    lifecycle_state: str
    already_applied: bool
    telemetry_preserved: bool = True
    physical_sensor_changed: bool = False


class DeviceLifecycleService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.devices = DeviceRepository(session)

    def remove(
        self,
        device_id: str,
        *,
        reason: str | None,
        connectivity_state: str,
        method: str = "dashboard_confirmed",
        factory_reset_requested: bool = False,
    ) -> LifecycleResult:
        device = self.devices.get(device_id)
        if device is None:
            raise LifecycleError("device_not_found", "Sensor registration was not found.")
        if device.archived or device.lifecycle_state == "removed":
            return LifecycleResult("existing", "removed", device_id, "removed", True)
        operation_id = uuid4().hex
        now = utc_now()
        try:
            device.archived = True
            device.enabled = False
            device.lifecycle_state = "removed"
            device.removed_at = now
            device.removal_reason = reason
            device.connection_status = "disabled"
            installations = self.session.query(SensorInstallation).filter(SensorInstallation.node_id == device_id).all()
            for installation in installations:
                installation.archived = True
                installation.enabled = False
                installation.provisioning_state = "removed"
                installation.updated_at = now
            self.session.add(DeviceLifecycleEvent(
                operation_id=operation_id, event_type="gateway_removed", device_id=device.device_id,
                display_name=device.display_name, hardware_id=device.hardware_id, ble_address=device.ble_address,
                connectivity_state=connectivity_state, method=method,
                factory_reset_requested=factory_reset_requested, result="success",
                detail_json=json.dumps({"reason": reason, "installations_archived": len(installations)}, separators=(",", ":")),
            ))
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return LifecycleResult(operation_id, "removed", device_id, "removed", False)

    def restore(self, device_id: str, *, expected_hardware_id: str | None, expected_ble_address: str | None) -> LifecycleResult:
        device = self.devices.get(device_id)
        if device is None:
            raise LifecycleError("device_not_found", "Removed sensor registration was not found.")
        if not device.archived and device.lifecycle_state == "active":
            return LifecycleResult("existing", "active", device_id, "active", True)
        if device.hardware_id and expected_hardware_id != device.hardware_id:
            raise LifecycleError("hardware_identity_mismatch", "Physical hardware identity does not match the removed sensor.")
        if device.ble_address and (
            not expected_ble_address or expected_ble_address.casefold() != device.ble_address.casefold()
        ):
            raise LifecycleError("ble_identity_mismatch", "BLE address does not match the removed sensor registration.")
        if device.hardware_id:
            conflict = self.devices.get_other_by_hardware_id(device.hardware_id, device.id)
            if conflict is not None:
                raise LifecycleError(
                    "hardware_identity_conflict", "Hardware identity is associated with another sensor record; resolve it first."
                )
        operation_id = uuid4().hex
        try:
            device.archived = False
            device.enabled = True
            device.lifecycle_state = "active"
            device.removed_at = None
            device.removal_reason = None
            device.connection_status = "disconnected"
            self.session.add(DeviceLifecycleEvent(
                operation_id=operation_id, event_type="gateway_restored", device_id=device.device_id,
                display_name=device.display_name, hardware_id=device.hardware_id, ble_address=device.ble_address,
                connectivity_state="disconnected", method="dashboard_confirmed",
                factory_reset_requested=False, result="success",
                detail_json=json.dumps({"identity_verified": True}, separators=(",", ":")),
            ))
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return LifecycleResult(operation_id, "active", device_id, "active", False)

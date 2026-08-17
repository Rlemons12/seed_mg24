import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from gateway.app.models import RegisteredDevice, SensorInstallation, SensorReregistrationWorkflow


class WorkflowError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


STATES = (
    "sensor_selected", "usb_connection_required", "physical_identity_verified", "configuration_backup_ready",
    "reset_confirmation_required", "reset_in_progress", "waiting_for_usb_reenumeration", "post_reset_verification",
    "unprovisioned_ready_for_registration", "registration_details_required", "searching_for_reset_sensor_ble",
    "ble_identity_matched", "provisioning_in_progress", "gateway_registration_in_progress",
    "network_verification_in_progress", "complete", "recoverable_error", "manual_recovery_required",
)


class ResetReregisterWorkflowService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def start(self, device: RegisteredDevice) -> SensorReregistrationWorkflow:
        if not device.hardware_id:
            raise WorkflowError("hardware_id_required", "Associate the immutable MCU hardware ID before starting reset.")
        active = self.session.scalar(select(SensorReregistrationWorkflow).where(
            SensorReregistrationWorkflow.source_record_id == device.id,
            SensorReregistrationWorkflow.state.not_in(("complete", "manual_recovery_required")),
        ).order_by(SensorReregistrationWorkflow.started_at.desc()))
        if active:
            return active
        workflow = SensorReregistrationWorkflow(
            operation_id=uuid4().hex, source_record_id=device.id, source_device_id=device.device_id,
            source_display_name=device.display_name, hardware_id=device.hardware_id,
            source_ble_address=device.ble_address, state="usb_connection_required",
            result_json=json.dumps({"firmware_version": device.firmware_version, "last_telemetry_at":
                                   device.last_seen_at.isoformat() if device.last_seen_at else None}),
        )
        self.session.add(workflow)
        self.session.commit()
        return workflow

    def get(self, operation_id: str) -> SensorReregistrationWorkflow:
        item = self.session.get(SensorReregistrationWorkflow, operation_id)
        if item is None:
            raise WorkflowError("workflow_not_found", "Reset and re-register operation was not found.")
        return item

    def transition(self, item: SensorReregistrationWorkflow, state: str, *, progress: str | None = None,
                   error_code: str | None = None, error_message: str | None = None) -> None:
        if state not in STATES:
            raise WorkflowError("invalid_state", "Workflow state is invalid.")
        entries = json.loads(item.progress_json)
        if progress and (not entries or entries[-1] != progress):
            entries.append(progress)
        item.progress_json = json.dumps(entries[-40:])
        item.state, item.error_code, item.error_message = state, error_code, error_message
        item.updated_at = datetime.now(UTC)
        self.session.commit()

    @staticmethod
    def public(item: SensorReregistrationWorkflow) -> dict:
        # Deliberately excludes reset challenges, confirmation tokens, and configuration values.
        result = json.loads(item.result_json)
        return {
            "operation_id": item.operation_id, "state": item.state, "source_record_id": item.source_record_id,
            "source_device_id": item.source_device_id, "source_display_name": item.source_display_name,
            "hardware_id": item.hardware_id, "source_ble_address": item.source_ble_address,
            "selected_port": item.selected_port, "backup_status": item.backup_status,
            "registration_choice": item.registration_choice, "target_device_id": item.target_device_id,
            "target_display_name": item.target_display_name, "target_location": item.target_location,
            "target_ble_address": item.target_ble_address, "progress": json.loads(item.progress_json),
            "result": result, "error": ({"code": item.error_code, "message": item.error_message}
                                       if item.error_code else None),
            "started_at": item.started_at.isoformat(), "updated_at": item.updated_at.isoformat(),
        }

    def safe_backup(self, item: SensorReregistrationWorkflow, sensor_state: dict) -> None:
        readback = sensor_state.get("configuration", sensor_state.get("readback", {}))
        allowed = {key: readback[key] for key in (
            "sample", "process", "report", "heartbeat", "filter", "window", "enabled"
        ) if key in readback}
        installations = self.session.scalars(select(SensorInstallation).where(
            SensorInstallation.node_id == item.source_device_id
        )).all()
        item.backup_json = json.dumps({"configuration": allowed, "installations": [{
            "installation_id": row.installation_id, "display_name": row.display_name,
            "location": row.location, "configuration": json.loads(row.configuration_json),
        } for row in installations]})
        item.backup_status = "complete"
        result = json.loads(item.result_json)
        result["backup_categories"] = ["identity", "sampling", "filtering", "reporting", "installation metadata"]
        item.result_json = json.dumps(result)
        self.transition(item, "configuration_backup_ready", progress="Application configuration backup verified")

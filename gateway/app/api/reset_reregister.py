import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from gateway.app.database import get_session
from gateway.app.models import (
    DeviceLifecycleEvent,
    Reading,
    RegisteredDevice,
    SensorInstallation,
    SensorReregistrationWorkflow,
)
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.request_security import require_bounded_same_origin_json, require_loopback
from gateway.app.schemas import DeviceCreate, InstallationConfiguration
from gateway.app.services.ble_provisioning import BleProvisioningError
from gateway.app.services.device_lifecycle_service import DeviceLifecycleService
from gateway.app.services.device_service import DeviceService
from gateway.app.services.reset_reregister_workflow import ResetReregisterWorkflowService, WorkflowError
from gateway.app.services.usb_factory_reset import UsbResetError

router = APIRouter(prefix="/api/reset-reregister", tags=["reset and re-register"])


def protect(request: Request, usb: bool = False) -> None:
    require_bounded_same_origin_json(request)
    if usb:
        require_loopback(request)


def fail(exc: WorkflowError, status: int = 409):
    raise HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)}) from exc


class StartRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=96)


class UsbSelection(BaseModel):
    port: str = Field(min_length=2, max_length=128)
    expected_hardware_id: str = Field(pattern=r"^0x[0-9A-F]{16}$")


class ConfirmReset(UsbSelection):
    typed_hardware_id: str = Field(pattern=r"^0x[0-9A-F]{16}$")


class RegistrationChoice(BaseModel):
    choice: str = Field(pattern=r"^(restore|new)$")
    device_id: str = Field(pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$", max_length=31)
    display_name: str = Field(min_length=1, max_length=160)
    location: str | None = Field(default=None, max_length=240)
    configuration: InstallationConfiguration


class BleSelection(BaseModel):
    address: str = Field(min_length=3, max_length=128)


class OperationMatch(BaseModel):
    expected_hardware_id: str = Field(pattern=r"^0x[0-9A-F]{16}$")


@router.post("/start")
def start(body: StartRequest, request: Request, session: Session = Depends(get_session)) -> dict:
    protect(request)
    device = DeviceRepository(session).get(body.device_id)
    if device is None or device.archived:
        raise HTTPException(404, detail={"code": "device_not_found", "message": "Select an active sensor record."})
    try:
        item = ResetReregisterWorkflowService(session).start(device)
    except WorkflowError as exc:
        fail(exc)
    return ResetReregisterWorkflowService.public(item)


@router.get("/incomplete")
def incomplete(session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(select(SensorReregistrationWorkflow).where(
        SensorReregistrationWorkflow.state != "complete")).all()
    return [ResetReregisterWorkflowService.public(row) for row in rows]


@router.get("/{operation_id}")
def status(operation_id: str, session: Session = Depends(get_session)) -> dict:
    try:
        return ResetReregisterWorkflowService.public(ResetReregisterWorkflowService(session).get(operation_id))
    except WorkflowError as exc:
        fail(exc, 404)


@router.post("/{operation_id}/detect-usb")
def detect_usb(operation_id: str, request: Request, session: Session = Depends(get_session)) -> dict:
    protect(request, usb=True)
    service = ResetReregisterWorkflowService(session)
    try:
        item = service.get(operation_id)
    except WorkflowError as exc:
        fail(exc, 404)
    boards = []
    for port in request.app.state.usb_factory_reset.ports():
        try:
            state = request.app.state.usb_factory_reset.inspect(port["port"])
            state["identity_match"] = state.get("hardware_id") == item.hardware_id
            boards.append(state)
        except (OSError, ValueError, RuntimeError) as exc:
            boards.append({**port, "status": "unreadable", "error": str(exc), "identity_match": False})
    return {"operation_id": operation_id, "expected_hardware_id": item.hardware_id, "boards": boards,
            "auto_select_port": boards[0].get("port") if len(boards) == 1 and boards[0].get("identity_match") else None}


@router.post("/{operation_id}/select-usb")
def select_usb(operation_id: str, body: UsbSelection, request: Request, session: Session = Depends(get_session)) -> dict:
    protect(request, usb=True)
    service = ResetReregisterWorkflowService(session)
    try:
        item = service.get(operation_id)
        if body.expected_hardware_id != item.hardware_id:
            raise WorkflowError("hardware_identity_mismatch", "Selected gateway and physical identities do not match.")
        state = request.app.state.usb_factory_reset.inspect(body.port)
        if state.get("hardware_id") != item.hardware_id:
            raise WorkflowError("hardware_identity_mismatch", "The sensor on this USB port is not the selected sensor.")
        item.selected_port = body.port
        result = json.loads(item.result_json)
        result.update({"firmware_version": state.get("firmware_version"), "usb_state": {
            key: state.get(key) for key in ("sensor_id", "display_name", "hardware_id", "firmware_version",
                                             "provisioning_state", "reset_state")}})
        item.result_json = json.dumps(result)
        service.transition(item, "physical_identity_verified", progress="Immutable USB hardware identity verified")
        return service.public(item)
    except (WorkflowError, UsbResetError) as exc:
        fail(exc if isinstance(exc, WorkflowError) else WorkflowError("usb_inspection_failed", str(exc)))


@router.post("/{operation_id}/backup")
def backup(operation_id: str, request: Request, session: Session = Depends(get_session)) -> dict:
    protect(request, usb=True)
    service = ResetReregisterWorkflowService(session)
    try:
        item = service.get(operation_id)
        if not item.selected_port:
            raise WorkflowError("usb_not_verified", "Verify the USB-connected sensor first.")
        state = request.app.state.usb_factory_reset.inspect(item.selected_port)
        if state.get("hardware_id") != item.hardware_id:
            raise WorkflowError("hardware_identity_mismatch", "USB identity changed before backup.")
        service.safe_backup(item, state)
        return {**service.public(item), "backup_categories": ["identity", "sampling", "filtering", "reporting",
                                                                   "installation metadata"]}
    except (WorkflowError, UsbResetError) as exc:
        fail(exc if isinstance(exc, WorkflowError) else WorkflowError("backup_failed", str(exc)))


@router.post("/{operation_id}/prepare-reset")
def prepare_reset(operation_id: str, body: ConfirmReset, request: Request,
                  session: Session = Depends(get_session)) -> dict:
    protect(request, usb=True)
    service = ResetReregisterWorkflowService(session)
    try:
        item = service.get(operation_id)
        if item.backup_status != "complete":
            raise WorkflowError("backup_required", "A verified application configuration backup is required.")
        if body.expected_hardware_id != item.hardware_id or body.typed_hardware_id != item.hardware_id:
            raise WorkflowError("hardware_identity_mismatch", "Type the exact immutable hardware ID.")
        if body.port != item.selected_port:
            raise WorkflowError("usb_port_mismatch", "The confirmed USB port changed; detect the sensor again.")
        token, state = request.app.state.usb_factory_reset.prepare(
            item.source_record_id, item.source_device_id, item.hardware_id, body.port)
        request.app.state.reregister_confirmations[operation_id] = token
        result = json.loads(item.result_json)
        result["reset_operation_id"] = state.get("reset_operation_id")
        item.result_json = json.dumps(result)
        service.transition(item, "reset_confirmation_required", progress="Device-bound reset confirmation prepared")
        return {**service.public(item), "confirmation_ready": True, "expires_in_seconds": 120}
    except (WorkflowError, UsbResetError) as exc:
        request.app.state.reregister_confirmations.pop(operation_id, None)
        fail(exc if isinstance(exc, WorkflowError) else WorkflowError("reset_prepare_failed", str(exc)))


@router.post("/{operation_id}/execute-reset", status_code=202)
async def execute_reset(operation_id: str, body: ConfirmReset, request: Request,
                        session: Session = Depends(get_session)) -> dict:
    protect(request, usb=True)
    service = ResetReregisterWorkflowService(session)
    token = request.app.state.reregister_confirmations.pop(operation_id, None)
    try:
        item = service.get(operation_id)
        if not token or body.expected_hardware_id != item.hardware_id or body.typed_hardware_id != item.hardware_id \
                or body.port != item.selected_port:
            raise WorkflowError("invalid_confirmation", "Reset confirmation is missing, expired, used, or mismatched.")
        request.app.state.usb_factory_reset.authorize(
            token, item.source_record_id, item.source_device_id, item.hardware_id, body.port)
        device = session.get(RegisteredDevice, item.source_record_id)
        device.factory_reset_status = "reset_pending"
        reset = request.app.state.usb_factory_reset.launch(
            item.source_record_id, item.source_device_id, item.hardware_id, body.port)
        item.reset_operation_id = reset.operation_id
        service.transition(item, "reset_in_progress", progress="Factory reset accepted by the USB service")
        asyncio.create_task(_monitor_reset(request, operation_id, reset.operation_id))
        return service.public(item)
    except (WorkflowError, UsbResetError) as exc:
        fail(exc if isinstance(exc, WorkflowError) else WorkflowError("reset_execution_failed", str(exc)))


@router.post("/{operation_id}/cancel-reset")
def cancel_reset(operation_id: str, body: ConfirmReset, request: Request,
                 session: Session = Depends(get_session)) -> dict:
    protect(request, usb=True)
    service = ResetReregisterWorkflowService(session)
    token = request.app.state.reregister_confirmations.pop(operation_id, None)
    try:
        item = service.get(operation_id)
        if not token or body.expected_hardware_id != item.hardware_id or body.typed_hardware_id != item.hardware_id \
                or body.port != item.selected_port:
            raise WorkflowError("invalid_confirmation", "Reset confirmation is missing, expired, used, or mismatched.")
        request.app.state.usb_factory_reset.cancel(
            token, item.source_record_id, item.source_device_id, item.hardware_id, body.port)
        service.transition(item, "configuration_backup_ready", progress="Prepared reset was cancelled safely")
        return service.public(item)
    except (WorkflowError, UsbResetError) as exc:
        fail(exc if isinstance(exc, WorkflowError) else WorkflowError("cancel_failed", str(exc)))


async def _monitor_reset(request: Request, workflow_id: str, reset_id: str) -> None:
    reset = request.app.state.usb_factory_reset.operations[reset_id]
    reported_state = None
    while reset.state not in {"failed", "physical_complete"}:
        workflow_state = "waiting_for_usb_reenumeration" if reset.state == "rebooting" else "reset_in_progress"
        if workflow_state != reported_state:
            with request.app.state.session_factory() as session:
                service = ResetReregisterWorkflowService(session)
                item = service.get(workflow_id)
                service.transition(item, workflow_state, progress={
                    "queued": "Preparing reset", "preparing": "Writing recovery marker and clearing application configuration",
                    "rebooting": "Waiting for USB reboot and re-enumeration",
                }.get(reset.state, "Factory reset is in progress"))
            reported_state = workflow_state
        await asyncio.sleep(0.1)
    with request.app.state.session_factory() as session:
        service = ResetReregisterWorkflowService(session)
        item = service.get(workflow_id)
        if not reset.physical_reset_complete:
            service.transition(item, "recoverable_error", progress="Factory reset failed", error_code="reset_failed",
                               error_message=reset.error or "Physical reset did not complete.")
            return
        service.transition(item, "post_reset_verification", progress="Verifying factory-default state after reboot")
        device = session.get(RegisteredDevice, item.source_record_id)
        try:
            DeviceLifecycleService(session).remove(
                device.device_id, reason="physical_factory_reset_verified", connectivity_state="resetting",
                method="reset_reregister_workflow", factory_reset_requested=True)
            device = session.get(RegisteredDevice, item.source_record_id)
            device.factory_reset_status = "complete"
            session.commit()
            await request.app.state.ble_manager.remove(item.source_device_id)
            result = json.loads(item.result_json)
            result["post_reset"] = {"hardware_id": reset.hardware_id,
                                    "firmware_version": (reset.post_reset or {}).get("firmware_version"),
                                    "unprovisioned": True, "reset_marker_complete": True,
                                    "telemetry_disabled": True, "bootstrap_available": True}
            item.result_json = json.dumps(result)
            service.transition(item, "unprovisioned_ready_for_registration",
                               progress="Post-reboot unprovisioned state verified")
        except Exception as exc:
            service.transition(item, "recoverable_error", progress="Physical reset verified; gateway cleanup failed",
                               error_code="gateway_cleanup_failed", error_message=str(exc)[:500])


@router.post("/{operation_id}/reconcile-reset")
async def reconcile_reset(operation_id: str, body: OperationMatch, request: Request,
                          session: Session = Depends(get_session)) -> dict:
    """Read back after a gateway restart; never repeats the destructive reset command."""
    protect(request, usb=True)
    service = ResetReregisterWorkflowService(session)
    try:
        item = service.get(operation_id)
        if body.expected_hardware_id != item.hardware_id:
            raise WorkflowError("hardware_identity_mismatch", "Recovery identity does not match this operation.")
        matches = []
        for candidate in request.app.state.usb_factory_reset.ports():
            try:
                state = request.app.state.usb_factory_reset.inspect(candidate["port"])
            except (OSError, ValueError, RuntimeError):
                continue
            if state.get("hardware_id") == item.hardware_id:
                matches.append(state)
        if len(matches) != 1:
            raise WorkflowError("usb_recovery_ambiguous", "Connect exactly one USB sensor matching this operation.")
        state = matches[0]
        verified = (
            state.get("node_id") is None and state.get("identity_status") == "unprovisioned"
            and state.get("reset_marker_state") in {None, "complete", "absent"}
            and state.get("telemetry_enabled") is not True
        )
        if not verified:
            raise WorkflowError("post_reset_unverified", "Sensor is not yet in the verified out-of-box state.")
        device = session.get(RegisteredDevice, item.source_record_id)
        if not device.archived:
            DeviceLifecycleService(session).remove(
                item.source_device_id, reason="physical_factory_reset_verified", connectivity_state="resetting",
                method="reset_reregister_recovery", factory_reset_requested=True)
        device = session.get(RegisteredDevice, item.source_record_id)
        device.factory_reset_status = "complete"
        result = json.loads(item.result_json)
        result["post_reset"] = {"hardware_id": state["hardware_id"],
                                "firmware_version": state.get("firmware_version"), "unprovisioned": True,
                                "reset_marker_complete": True, "telemetry_disabled": True,
                                "bootstrap_available": bool(state.get("bootstrap_available", True))}
        item.result_json = json.dumps(result)
        service.transition(item, "unprovisioned_ready_for_registration",
                           progress="Gateway restart recovery verified from physical read-back")
        await request.app.state.ble_manager.remove(item.source_device_id)
        return service.public(item)
    except WorkflowError as exc:
        fail(exc)


@router.post("/{operation_id}/registration")
def registration(operation_id: str, body: RegistrationChoice, request: Request,
                 session: Session = Depends(get_session)) -> dict:
    protect(request)
    service = ResetReregisterWorkflowService(session)
    try:
        item = service.get(operation_id)
        if item.state not in {"unprovisioned_ready_for_registration", "registration_details_required",
                              "recoverable_error"}:
            raise WorkflowError("invalid_workflow_state", "Physical reset verification must complete first.")
        existing = DeviceRepository(session).get(body.device_id)
        if body.choice == "restore":
            if body.device_id != item.source_device_id or body.display_name != item.source_display_name:
                raise WorkflowError("restore_identity_mismatch", "Restore must use the explicitly confirmed prior identity.")
        elif existing is not None and existing.id != item.source_record_id:
            raise WorkflowError("duplicate_sensor_id", "That sensor ID already belongs to another record.")
        elif body.choice == "new" and existing is not None:
            raise WorkflowError("new_identity_requires_new_id", "A new registration must use a new unused sensor ID.")
        active_hw = session.scalar(select(RegisteredDevice).where(
            RegisteredDevice.hardware_id == item.hardware_id, RegisteredDevice.archived.is_(False),
            RegisteredDevice.id != item.source_record_id))
        if active_hw:
            raise WorkflowError("hardware_identity_collision", "This hardware identity is already active.")
        item.registration_choice, item.target_device_id = body.choice, body.device_id
        item.target_display_name, item.target_location = body.display_name.strip(), body.location
        item.configuration_json = body.configuration.model_dump_json()
        session.add(DeviceLifecycleEvent(
            operation_id=f"{item.operation_id}-choice", event_type="registration_choice", device_id=body.device_id,
            display_name=body.display_name, hardware_id=item.hardware_id, ble_address=None,
            connectivity_state="unprovisioned", method="reset_reregister_workflow", factory_reset_requested=True,
            result="accepted", detail_json=json.dumps({"registration_choice": body.choice})))
        service.transition(item, "searching_for_reset_sensor_ble", progress="Registration choice validated")
        return service.public(item)
    except WorkflowError as exc:
        fail(exc)


@router.post("/{operation_id}/scan-ble")
async def scan_ble(operation_id: str, request: Request, session: Session = Depends(get_session)) -> dict:
    protect(request)
    service = ResetReregisterWorkflowService(session)
    try:
        item = service.get(operation_id)
        await request.app.state.scanner.start_scan()
        await request.app.state.scanner.wait_for_scan()
        candidates = []
        for discovery in request.app.state.scanner.discoveries():
            if not discovery.compatible:
                continue
            try:
                state = await request.app.state.node_provisioner.read_state(discovery.address)
            except (BleProvisioningError, TimeoutError, ConnectionError):
                continue
            if state["readback"].get("id") == "UNASSIGNED-MG24":
                candidates.append({"address": discovery.address, "name": discovery.name, "rssi": discovery.rssi,
                                   "commissioning_state": "unassigned",
                                   "hardware_identity_available": False})
        return {
            **service.public(item), "candidates": candidates,
            "identity_limitation": (
                "BLE onboarding does not expose immutable MCU hardware ID; select explicitly and correlate physical "
                "presence. Multiple candidates are never auto-selected."
            ),
        }
    except WorkflowError as exc:
        fail(exc, 404)


@router.post("/{operation_id}/select-ble")
def select_ble(operation_id: str, body: BleSelection, request: Request, session: Session = Depends(get_session)) -> dict:
    protect(request)
    service = ResetReregisterWorkflowService(session)
    try:
        item = service.get(operation_id)
        discovery = request.app.state.scanner.get(body.address)
        if discovery is None or not discovery.compatible:
            raise WorkflowError("ble_candidate_expired", "Selected BLE candidate expired; scan again.")
        item.target_ble_address = body.address
        service.transition(item, "ble_identity_matched", progress="Operator selected the unprovisioned BLE candidate")
        return service.public(item)
    except WorkflowError as exc:
        fail(exc)


@router.post("/{operation_id}/provision", status_code=202)
async def provision(operation_id: str, request: Request, session: Session = Depends(get_session)) -> dict:
    protect(request)
    service = ResetReregisterWorkflowService(session)
    try:
        item = service.get(operation_id)
        if not item.target_ble_address or not item.configuration_json:
            raise WorkflowError("ble_target_required", "Select a verified unprovisioned BLE candidate first.")
        service.transition(item, "provisioning_in_progress", progress="BLE provisioning started")
        result = await request.app.state.node_provisioner.provision(
            item.target_ble_address, item.target_device_id, item.operation_id[:16], json.loads(item.configuration_json))
        if result["readback"].get("id") != item.target_device_id:
            raise WorkflowError("provisioning_readback_failed", "Provisioned identity did not pass read-back.")
        result_data = json.loads(item.result_json)
        result_data["sensor_provisioned"] = True
        item.result_json = json.dumps(result_data)
        session.add(DeviceLifecycleEvent(
            operation_id=f"{item.operation_id}-provision", event_type="ble_provisioned", device_id=item.target_device_id,
            display_name=item.target_display_name, hardware_id=item.hardware_id, ble_address=item.target_ble_address,
            connectivity_state="connecting", method="reset_reregister_workflow", factory_reset_requested=True,
            result="success", detail_json="{}"))
        service.transition(item, "gateway_registration_in_progress", progress="BLE provisioning read-back verified")
        source = session.get(RegisteredDevice, item.source_record_id)
        if item.registration_choice == "restore":
            DeviceLifecycleService(session).restore(item.source_device_id, expected_hardware_id=item.hardware_id,
                                                    expected_ble_address=item.target_ble_address)
            source.display_name, source.location, source.factory_reset_status = item.target_display_name, item.target_location, "complete"
            for installation in session.scalars(select(SensorInstallation).where(
                    SensorInstallation.node_id == source.device_id)).all():
                installation.archived = False
                installation.enabled = True
                installation.provisioning_state = "active"
            device = source
        else:
            discovery = request.app.state.scanner.get(item.target_ble_address)
            device = DeviceService(DeviceRepository(session), request.app.state.settings.device_id_pattern).register(
                DeviceCreate(device_id=item.target_device_id, display_name=item.target_display_name,
                             discovery_address=item.target_ble_address, location=item.target_location),
                discovery.model_copy(update={"stable_device_id": item.target_device_id}))
            device.hardware_id, device.factory_reset_status = item.hardware_id, "complete"
        session.add(DeviceLifecycleEvent(
            operation_id=f"{item.operation_id}-network", event_type="gateway_network_added", device_id=device.device_id,
            display_name=device.display_name, hardware_id=item.hardware_id, ble_address=item.target_ble_address,
            connectivity_state="connecting", method="reset_reregister_workflow", factory_reset_requested=True,
            result="success", detail_json=json.dumps({"registration_choice": item.registration_choice})))
        session.commit()
        request.app.state.ble_manager.schedule(device.device_id, device.ble_address)
        result_data = json.loads(item.result_json)
        result_data.update({"registered_record_id": device.id, "gateway_registration": "active"})
        item.result_json = json.dumps(result_data)
        service.transition(item, "network_verification_in_progress", progress="Gateway registration is active")
        return service.public(item)
    except (WorkflowError, BleProvisioningError, IntegrityError) as exc:
        session.rollback()
        try:
            item = service.get(operation_id)
            sensor_provisioned = bool(json.loads(item.result_json).get("sensor_provisioned"))
            code = "gateway_registration_failed" if sensor_provisioned else "ble_provisioning_failed"
            progress = "Sensor provisioned; gateway registration requires retry" if sensor_provisioned \
                else "BLE provisioning requires retry"
            service.transition(item, "recoverable_error", error_code=code,
                               error_message=str(exc)[:500], progress=progress)
        except WorkflowError:
            pass
        fail(exc if isinstance(exc, WorkflowError) else WorkflowError("provision_or_registration_failed", str(exc)))


@router.post("/{operation_id}/verify-network")
def verify_network(operation_id: str, request: Request, session: Session = Depends(get_session)) -> dict:
    protect(request)
    service = ResetReregisterWorkflowService(session)
    try:
        item = service.get(operation_id)
        device = DeviceRepository(session).get(item.target_device_id or "")
        if device is None or device.archived or not device.enabled or device.hardware_id != item.hardware_id:
            raise WorkflowError("gateway_registration_unverified", "Active gateway identity has not been verified.")
        reading = session.scalar(select(Reading).where(Reading.registered_device_id == device.id,
                                                        Reading.received_at >= item.started_at)
                                 .order_by(Reading.received_at.desc()))
        connected = device.connection_status == "connected"
        verified = connected and reading is not None
        result = json.loads(item.result_json)
        result.update({"gateway_registration": "active", "ble_connection": "connected" if connected else "waiting",
                       "first_telemetry": "verified" if reading else "pending", "sensor_id": device.device_id,
                       "sensor_name": device.display_name, "location": device.location})
        item.result_json = json.dumps(result)
        service.transition(item, "complete" if verified else "network_verification_in_progress",
                           progress="First telemetry verified" if verified else "Registered—waiting for first telemetry")
        return service.public(item)
    except WorkflowError as exc:
        fail(exc)

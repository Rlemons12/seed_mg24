import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from gateway.app.database import get_session
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.request_security import require_bounded_same_origin_json, require_loopback
from gateway.app.services.device_lifecycle_service import DeviceLifecycleService
from gateway.app.services.usb_factory_reset import UsbResetError

router = APIRouter(prefix="/api/factory-reset", tags=["USB factory reset"])


def protect(request: Request) -> None:
    require_loopback(request)
    if request.method == "POST":
        require_bounded_same_origin_json(request)


class ResetTarget(BaseModel):
    device_id: str = Field(min_length=1, max_length=96)
    hardware_id: str = Field(pattern=r"^0x[0-9A-F]{16}$")
    port: str = Field(min_length=2, max_length=128)


class ResetExecution(ResetTarget):
    confirmation_token: str = Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")


class CleanupRetry(ResetTarget):
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")


@router.get("/boards")
def boards(request: Request) -> list[dict]:
    protect(request)
    rows = []
    for item in request.app.state.usb_factory_reset.ports():
        try:
            rows.append(request.app.state.usb_factory_reset.inspect(item["port"]))
        except (OSError, ValueError, RuntimeError) as exc:
            rows.append({**item, "status": "unreadable", "error": str(exc)})
    return rows


@router.post("/confirm")
def confirm(body: ResetTarget, request: Request, session: Session = Depends(get_session)) -> dict:
    protect(request)
    device = DeviceRepository(session).get(body.device_id)
    if device is None or device.archived:
        raise HTTPException(status_code=404, detail={"code": "device_not_found", "message": "Active sensor not found."})
    if device.hardware_id and device.hardware_id != body.hardware_id:
        raise HTTPException(status_code=409, detail={"code": "hardware_identity_mismatch", "message": "Hardware identity mismatch."})
    try:
        token, state = request.app.state.usb_factory_reset.prepare(device.id, body.device_id, body.hardware_id, body.port)
    except UsbResetError as exc:
        raise HTTPException(status_code=409, detail={"code": "physical_identity_mismatch", "message": str(exc)}) from exc
    if not device.hardware_id:
        device.hardware_id = body.hardware_id
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            request.app.state.usb_factory_reset.cancel(token, device.id, body.device_id, body.hardware_id, body.port)
            raise HTTPException(
                status_code=409,
                detail={"code": "hardware_identity_conflict", "message": "Hardware identity belongs to another record."},
            ) from exc
    return {"confirmation_token": token, "expires_in_seconds": 120, "device_id": device.device_id,
            "display_name": device.display_name, "hardware_id": body.hardware_id, "port": body.port,
            "firmware_version": state.get("firmware_version"), "provisioning_state": state.get("provisioning_state")}


@router.post("/execute", status_code=202)
async def execute(body: ResetExecution, request: Request, session: Session = Depends(get_session)) -> dict:
    protect(request)
    device = DeviceRepository(session).get(body.device_id)
    if device is None or device.archived:
        raise HTTPException(status_code=409, detail={"code": "target_changed", "message": "Reset target changed after confirmation."})
    try:
        item = request.app.state.usb_factory_reset.start(
            body.confirmation_token, device.id, body.device_id, body.hardware_id, body.port
        )
        asyncio.create_task(finalize_when_ready(request, item.operation_id))
        return vars(item)
    except UsbResetError as exc:
        raise HTTPException(status_code=409, detail={"code": "invalid_confirmation", "message": str(exc)}) from exc


@router.post("/cancel")
def cancel(body: ResetExecution, request: Request, session: Session = Depends(get_session)) -> dict:
    protect(request)
    device = DeviceRepository(session).get(body.device_id)
    if device is None:
        raise HTTPException(status_code=404, detail={"code": "device_not_found", "message": "Sensor not found."})
    try:
        request.app.state.usb_factory_reset.cancel(
            body.confirmation_token, device.id, body.device_id, body.hardware_id, body.port
        )
    except UsbResetError as exc:
        raise HTTPException(status_code=409, detail={"code": "invalid_confirmation", "message": str(exc)}) from exc
    return {"status": "cancelled"}


async def cleanup_gateway(request: Request, item) -> None:
    try:
        with request.app.state.session_factory() as session:
            device = DeviceRepository(session).get(item.device_id)
            if device is None or device.id != item.record_id or device.hardware_id != item.hardware_id:
                raise UsbResetError("gateway sensor record changed during physical reset")
            DeviceLifecycleService(session).remove(
                item.device_id, reason="physical_factory_reset_verified", connectivity_state="resetting",
                method="usb_factory_reset", factory_reset_requested=True,
            )
            device = DeviceRepository(session).get(item.device_id)
            device.factory_reset_status = "complete"
            session.commit()
        await request.app.state.ble_manager.remove(item.device_id)
        item.gateway_cleanup_complete = True
        item.state = "complete"
        item.error = None
        if "gateway_registration_removed_history_preserved" not in item.progress:
            item.progress.append("gateway_registration_removed_history_preserved")
    except Exception as exc:
        item.state = "partial_failure"
        item.error = f"Physical reset verified; gateway cleanup requires retry: {str(exc)[:300]}"


async def finalize_when_ready(request: Request, operation_id: str) -> None:
    item = request.app.state.usb_factory_reset.operations[operation_id]
    while item.state not in {"failed", "physical_complete"}:
        await asyncio.sleep(0.1)
    if item.physical_reset_complete:
        await cleanup_gateway(request, item)


@router.get("/operations/{operation_id}")
async def operation(operation_id: str, request: Request, session: Session = Depends(get_session)) -> dict:
    protect(request)
    item = request.app.state.usb_factory_reset.operations.get(operation_id)
    if item is None:
        raise HTTPException(status_code=404, detail={"code": "operation_not_found", "message": "Reset operation not found."})
    return vars(item)


@router.post("/operations/{operation_id}/retry-cleanup", status_code=202)
async def retry_cleanup(operation_id: str, body: CleanupRetry, request: Request) -> dict:
    protect(request)
    item = request.app.state.usb_factory_reset.operations.get(operation_id)
    if (
        item is None or body.operation_id != operation_id or body.device_id != item.device_id
        or body.hardware_id != item.hardware_id or body.port != item.port or not item.physical_reset_complete
    ):
        raise HTTPException(status_code=409, detail={"code": "operation_mismatch", "message": "Cleanup target mismatch."})
    if not item.gateway_cleanup_complete:
        await cleanup_gateway(request, item)
    return vars(item)

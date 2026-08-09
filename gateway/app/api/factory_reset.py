from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from gateway.app.database import get_session
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.services.device_lifecycle_service import DeviceLifecycleService
from gateway.app.services.usb_factory_reset import UsbResetError

router = APIRouter(prefix="/api/factory-reset", tags=["USB factory reset"])


def protect(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail={"code": "loopback_required", "message": "USB reset is loopback-only."})
    origin = request.headers.get("origin")
    if request.method == "POST" and origin and urlsplit(origin).hostname != request.url.hostname:
        raise HTTPException(status_code=403, detail={"code": "same_origin_required", "message": "Cross-origin reset denied."})


class ResetTarget(BaseModel):
    device_id: str = Field(min_length=1, max_length=96)
    hardware_id: str = Field(pattern=r"^0x[0-9A-F]{16}$")
    port: str = Field(min_length=2, max_length=128)


class ResetExecution(ResetTarget):
    confirmation_token: str = Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")


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
        token, state = request.app.state.usb_factory_reset.prepare(body.device_id, body.hardware_id, body.port)
    except UsbResetError as exc:
        raise HTTPException(status_code=409, detail={"code": "physical_identity_mismatch", "message": str(exc)}) from exc
    if not device.hardware_id:
        device.hardware_id = body.hardware_id
        session.commit()
    return {"confirmation_token": token, "expires_in_seconds": 120, "device_id": device.device_id,
            "display_name": device.display_name, "hardware_id": body.hardware_id, "port": body.port,
            "firmware_version": state.get("firmware_version"), "provisioning_state": state.get("provisioning_state")}


@router.post("/execute", status_code=202)
def execute(body: ResetExecution, request: Request) -> dict:
    protect(request)
    try:
        return vars(request.app.state.usb_factory_reset.start(
            body.confirmation_token, body.device_id, body.hardware_id, body.port
        ))
    except UsbResetError as exc:
        raise HTTPException(status_code=409, detail={"code": "invalid_confirmation", "message": str(exc)}) from exc


@router.get("/operations/{operation_id}")
async def operation(operation_id: str, request: Request, session: Session = Depends(get_session)) -> dict:
    protect(request)
    item = request.app.state.usb_factory_reset.operations.get(operation_id)
    if item is None:
        raise HTTPException(status_code=404, detail={"code": "operation_not_found", "message": "Reset operation not found."})
    if item.physical_reset_complete and not item.gateway_cleanup_complete:
        try:
            DeviceLifecycleService(session).remove(
                item.device_id,
                reason="physical_factory_reset_verified",
                connectivity_state="resetting",
                method="usb_factory_reset",
                factory_reset_requested=True,
            )
            device = DeviceRepository(session).get(item.device_id)
            if device:
                device.factory_reset_status = "complete"
                session.commit()
            await request.app.state.ble_manager.remove(item.device_id)
            item.gateway_cleanup_complete = True
            item.state = "complete"
            item.progress.append("gateway_registration_removed_history_preserved")
        except Exception as exc:
            session.rollback()
            item.state = "partial_failure"
            item.error = f"Physical reset verified; gateway cleanup requires retry: {str(exc)[:300]}"
    return vars(item)

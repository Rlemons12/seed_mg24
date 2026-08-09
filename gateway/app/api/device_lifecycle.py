from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from gateway.app.database import get_session
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.schemas import (
    LifecycleConfirmationRequest,
    LifecycleConfirmationResponse,
    LifecycleExecuteRequest,
    LifecycleOperationResponse,
)
from gateway.app.services.device_lifecycle_service import DeviceLifecycleService, LifecycleError

router = APIRouter(prefix="/api/device-lifecycle", tags=["device lifecycle"])


def require_protected_browser_request(request: Request) -> None:
    if request.method != "POST":
        raise HTTPException(status_code=405, detail={"code": "method_not_allowed", "message": "POST is required."})
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail={"code": "json_required", "message": "application/json is required."})
    origin = request.headers.get("origin")
    if not origin and (not request.client or request.client.host != "testclient"):
        raise HTTPException(
            status_code=403, detail={"code": "same_origin_required", "message": "A same-origin browser request is required."}
        )
    if origin and urlsplit(origin).hostname != request.url.hostname:
        raise HTTPException(
            status_code=403, detail={"code": "same_origin_required", "message": "Cross-origin lifecycle requests are denied."}
        )


@router.get("/removed")
def removed_devices(session: Session = Depends(get_session)) -> list[dict]:
    return [
        {
            "device_id": item.device_id, "display_name": item.display_name, "hardware_id": item.hardware_id,
            "ble_address": item.ble_address, "removed_at": item.removed_at, "removal_reason": item.removal_reason,
            "lifecycle_state": item.lifecycle_state, "factory_reset_status": item.factory_reset_status,
        }
        for item in DeviceRepository(session).list(include_archived=True) if item.archived
    ]


@router.post("/confirm", response_model=LifecycleConfirmationResponse)
def prepare_confirmation(body: LifecycleConfirmationRequest, request: Request, session: Session = Depends(get_session)):
    require_protected_browser_request(request)
    device = DeviceRepository(session).get(body.device_id)
    if device is None:
        raise HTTPException(status_code=404, detail={"code": "device_not_found", "message": "Sensor registration was not found."})
    if body.expected_hardware_id and device.hardware_id and body.expected_hardware_id != device.hardware_id:
        raise HTTPException(status_code=409, detail={"code": "hardware_identity_mismatch", "message": "Hardware identity mismatch."})
    if body.operation == "remove" and device.archived:
        state = "removed"
    elif body.operation == "restore" and not device.archived:
        state = "active"
    else:
        state = device.lifecycle_state
    hardware_id = device.hardware_id or body.expected_hardware_id
    ble_address = device.ble_address
    token = request.app.state.lifecycle_confirmations.issue(body.operation, body.device_id, hardware_id, ble_address)
    return LifecycleConfirmationResponse(
        confirmation_token=token, operation=body.operation, device_id=device.device_id,
        display_name=device.display_name, hardware_id=hardware_id, ble_address=ble_address,
        connection_status=request.app.state.ble_manager.runtime(device.device_id)["connection_status"] if state != "removed" else "removed",
        expires_in_seconds=request.app.state.lifecycle_confirmations.ttl_seconds,
    )


@router.post("/execute", response_model=LifecycleOperationResponse)
async def execute(body: LifecycleExecuteRequest, request: Request, session: Session = Depends(get_session)):
    require_protected_browser_request(request)
    device = DeviceRepository(session).get(body.device_id)
    if device is None:
        raise HTTPException(status_code=404, detail={"code": "device_not_found", "message": "Sensor registration was not found."})
    hardware_id = device.hardware_id or body.expected_hardware_id
    try:
        request.app.state.lifecycle_confirmations.consume(
            body.confirmation_token, body.operation, body.device_id, hardware_id, device.ble_address
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "invalid_confirmation", "message": str(exc)}) from exc
    service = DeviceLifecycleService(session)
    try:
        if body.operation == "remove":
            runtime = request.app.state.ble_manager.runtime(body.device_id)["connection_status"]
            result = service.remove(body.device_id, reason=body.reason, connectivity_state=runtime)
            await request.app.state.ble_manager.remove(body.device_id)
            request.app.state.device_configuration_results = {
                key: value for key, value in request.app.state.device_configuration_results.items() if key[0] != body.device_id
            }
        else:
            result = service.restore(
                body.device_id, expected_hardware_id=body.expected_hardware_id,
                expected_ble_address=body.expected_ble_address or device.ble_address,
            )
            restored = DeviceRepository(session).get(body.device_id)
            if restored and restored.ble_address:
                request.app.state.ble_manager.schedule(restored.device_id, restored.ble_address)
    except LifecycleError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message}) from exc
    return LifecycleOperationResponse(**vars(result))

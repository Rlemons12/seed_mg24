from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from gateway.app.database import get_session
from gateway.app.repositories.device_repository import DeviceRepository, DuplicateDeviceError
from gateway.app.schemas import DeviceCreate, DeviceResponse, DeviceUpdate, Discovery
from gateway.app.services.device_service import DeviceService, DeviceValidationError

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _response(request: Request, device) -> DeviceResponse:
    runtime = request.app.state.ble_manager.runtime(device.device_id)
    if runtime["connection_status"] == "connected" and device.last_seen_at is not None:
        last_seen = device.last_seen_at
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - last_seen).total_seconds()
        if age > request.app.state.settings.stale_after_seconds:
            runtime["connection_status"] = "stale"
    data = DeviceResponse.model_validate(device).model_copy(update=runtime)
    discovery = request.app.state.scanner.get(device.ble_address) if device.ble_address else None
    return data.model_copy(update={"rssi": discovery.rssi if discovery else None})


@router.get("", response_model=list[DeviceResponse])
def list_devices(request: Request, session: Session = Depends(get_session)) -> list[DeviceResponse]:
    return [_response(request, item) for item in DeviceRepository(session).list()]


@router.post("/scan", status_code=status.HTTP_202_ACCEPTED)
async def start_scan(request: Request) -> dict:
    started = await request.app.state.scanner.start_scan()
    return {
        "started": started,
        "status": "scanning" if started else "already_scanning",
        "scan_duration_seconds": request.app.state.settings.scan_duration_seconds,
    }


@router.get("/discoveries", response_model=list[Discovery])
def discoveries(request: Request) -> list[Discovery]:
    return request.app.state.scanner.discoveries()


@router.post("", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def register_device(body: DeviceCreate, request: Request, session: Session = Depends(get_session)) -> DeviceResponse:
    discovery = request.app.state.scanner.get(body.discovery_address)
    if discovery is None:
        raise HTTPException(status_code=409, detail="selected discovery expired; scan again")
    service = DeviceService(DeviceRepository(session), request.app.state.settings.device_id_pattern)
    try:
        device = service.register(body, discovery)
    except DuplicateDeviceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DeviceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if device.enabled and device.ble_address:
        request.app.state.ble_manager.schedule(device.device_id, device.ble_address)
    return _response(request, device)


@router.get("/{device_id}", response_model=DeviceResponse)
def get_device(device_id: str, request: Request, session: Session = Depends(get_session)) -> DeviceResponse:
    device = DeviceRepository(session).get(device_id)
    if device is None or device.archived:
        raise HTTPException(status_code=404, detail="device not found")
    return _response(request, device)


@router.patch("/{device_id}", response_model=DeviceResponse)
async def update_device(device_id: str, body: DeviceUpdate, request: Request, session: Session = Depends(get_session)) -> DeviceResponse:
    repository = DeviceRepository(session)
    device = repository.get(device_id)
    if device is None or device.archived:
        raise HTTPException(status_code=404, detail="device not found")
    device = DeviceService(repository, request.app.state.settings.device_id_pattern).update(device, body)
    if body.enabled is False:
        await request.app.state.ble_manager.remove(device_id)
    elif body.enabled is True and device.ble_address:
        request.app.state.ble_manager.schedule(device_id, device.ble_address)
    return _response(request, device)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_device(device_id: str, request: Request, session: Session = Depends(get_session)) -> Response:
    repository = DeviceRepository(session)
    device = repository.get(device_id)
    if device is None or device.archived:
        raise HTTPException(status_code=404, detail="device not found")
    repository.update(device, archived=True, enabled=False, connection_status="disabled")
    await request.app.state.ble_manager.remove(device_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{device_id}/connect", status_code=status.HTTP_202_ACCEPTED)
def connect_device(device_id: str, request: Request, session: Session = Depends(get_session)) -> dict:
    device = DeviceRepository(session).get(device_id)
    if device is None or device.archived:
        raise HTTPException(status_code=404, detail="device not found")
    if not device.enabled:
        raise HTTPException(status_code=409, detail="device is disabled")
    if not device.ble_address:
        raise HTTPException(status_code=409, detail="device has no observed BLE address")
    request.app.state.ble_manager.schedule(device_id, device.ble_address)
    return {"status": "scheduled"}


@router.post("/{device_id}/disconnect", status_code=status.HTTP_202_ACCEPTED)
async def disconnect_device(device_id: str, request: Request, session: Session = Depends(get_session)) -> dict:
    if DeviceRepository(session).get(device_id) is None:
        raise HTTPException(status_code=404, detail="device not found")
    await request.app.state.ble_manager.remove(device_id)
    return {"status": "disconnected"}

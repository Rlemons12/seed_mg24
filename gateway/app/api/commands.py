from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from gateway.app.ble.manager import validate_command
from gateway.app.database import get_session
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.repositories.reading_repository import ReadingRepository
from gateway.app.schemas import CommandRequest, CommandResponse

router = APIRouter(prefix="/api/devices", tags=["commands"])


@router.post("/{device_id}/identify")
async def identify_device(device_id: str, request: Request, session: Session = Depends(get_session)) -> dict:
    device = DeviceRepository(session).get(device_id)
    if device is None or device.archived:
        raise HTTPException(status_code=404, detail="device not found")
    latest = ReadingRepository(session).latest(device.id)
    reported = next((row.normalized_value for row in latest if row.channel == "led_brightness"), 0)
    restore_brightness = max(0, min(255, round(reported or 0)))
    try:
        await request.app.state.ble_manager.identify(device_id, restore_brightness)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="device identification timed out") from exc
    return {"accepted": True, "device_id": device_id, "pattern": "three-short-one-long", "restored_brightness": restore_brightness}


@router.post("/{device_id}/commands", response_model=CommandResponse)
async def send_command(device_id: str, body: CommandRequest, request: Request, session: Session = Depends(get_session)) -> CommandResponse:
    device = DeviceRepository(session).get(device_id)
    if device is None or device.archived:
        raise HTTPException(status_code=404, detail="device not found")
    try:
        command = validate_command(body.command)
        await request.app.state.ble_manager.command(device_id, command)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="device command timed out") from exc
    return CommandResponse(accepted=True, command=command)

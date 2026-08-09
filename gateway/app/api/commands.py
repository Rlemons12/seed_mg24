from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from gateway.app.ble.manager import validate_command
from gateway.app.database import get_session
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.schemas import CommandRequest, CommandResponse

router = APIRouter(prefix="/api/devices", tags=["commands"])


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

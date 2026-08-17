import base64
import binascii
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from gateway.app.database import get_session
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.repositories.reading_repository import ReadingRepository
from gateway.app.schemas import ReadingPage, ReadingResponse

router = APIRouter(prefix="/api/devices", tags=["telemetry"])
ws_router = APIRouter()


def _device(session: Session, device_id: str):
    device = DeviceRepository(session).get(device_id)
    if device is None or device.archived:
        raise HTTPException(status_code=404, detail="device not found")
    return device


@router.get("/{device_id}/readings/latest", response_model=list[ReadingResponse])
def latest(device_id: str, session: Session = Depends(get_session)) -> list:
    device = _device(session, device_id)
    return ReadingRepository(session).latest(device.id)


@router.get("/{device_id}/readings", response_model=ReadingPage)
def history(
    device_id: str,
    request: Request,
    session: Session = Depends(get_session),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1),
    start: datetime | None = None,
    end: datetime | None = None,
    channel: str | None = Query(default=None, max_length=96),
    installation_id: str | None = Query(default=None, max_length=64),
    interface_id: str | None = Query(default=None, max_length=64),
    cursor: str | None = Query(default=None, max_length=256),
    include_total: bool = True,
) -> ReadingPage:
    settings = request.app.state.settings
    if limit > settings.history_page_size_max:
        raise HTTPException(status_code=422, detail=f"limit must not exceed {settings.history_page_size_max}")
    now = datetime.now(UTC)
    end = end or now
    start = start or end - timedelta(days=1)
    end = end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)
    start = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
    if end < start:
        raise HTTPException(status_code=422, detail="end must not precede start")
    if end - start > timedelta(days=settings.history_max_days):
        raise HTTPException(status_code=422, detail="requested history range is too large")
    before = None
    if cursor:
        try:
            decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
            cursor_time, cursor_id = decoded.rsplit("|", 1)
            before_at = datetime.fromisoformat(cursor_time)
            before = (before_at.replace(tzinfo=UTC) if before_at.tzinfo is None else before_at.astimezone(UTC), int(cursor_id))
        except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
            raise HTTPException(status_code=422, detail="cursor is invalid") from exc
    device = _device(session, device_id)
    items, total = ReadingRepository(session).history(
        device.id,
        offset=offset,
        limit=limit,
        start=start,
        end=end,
        channel=channel,
        installation_id=installation_id,
        interface_id=interface_id,
        before=before,
        include_total=include_total,
    )
    next_cursor = None
    if len(items) == limit:
        marker = f"{items[-1].received_at.isoformat()}|{items[-1].id}".encode()
        next_cursor = base64.urlsafe_b64encode(marker).decode()
    return ReadingPage(items=items, total=total, offset=offset, limit=limit, next_cursor=next_cursor)


@ws_router.websocket("/ws/telemetry")
async def telemetry_socket(websocket: WebSocket) -> None:
    manager = websocket.app.state.websocket_manager
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception:
        await manager.disconnect(websocket)

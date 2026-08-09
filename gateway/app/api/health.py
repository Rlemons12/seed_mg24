from datetime import UTC, datetime

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api")


@router.get("/health")
def health(request: Request) -> dict:
    return {
        "status": "ok",
        "version": request.app.state.version,
        "time": datetime.now(UTC).isoformat(),
        "managed_devices": len(request.app.state.ble_manager.connections),
    }

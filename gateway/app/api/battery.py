from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/devices/{device_id}/battery", tags=["battery"])


class MarkChargedRequest(BaseModel):
    occurred_at: datetime | None = None
    voltage: float | None = Field(default=None, gt=0, le=10)
    partial_charge: bool = False
    notes: str | None = Field(default=None, max_length=1000)


class ReplaceBatteryRequest(BaseModel):
    occurred_at: datetime | None = None
    voltage: float | None = Field(default=None, gt=0, le=10)
    reason: str = Field(min_length=1, max_length=240)
    notes: str | None = Field(default=None, max_length=1000)
    source: str = Field(default="operator", min_length=1, max_length=64)


def _not_found(exc: LookupError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.get("")
def summary(device_id: str, request: Request) -> dict:
    try:
        return request.app.state.battery_service.summary(device_id)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.get("/cycles")
def cycles(device_id: str, request: Request, limit: int = Query(100, ge=1, le=500)) -> list[dict]:
    try:
        return request.app.state.battery_service.cycles(device_id, limit=limit)
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.get("/cycles/{cycle_id}")
def cycle(device_id: str, cycle_id: int, request: Request) -> dict:
    try:
        result = request.app.state.battery_service.cycle(device_id, cycle_id)
    except LookupError as exc:
        raise _not_found(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="battery cycle not found")
    return result


@router.get("/history")
def history(
    device_id: str, request: Request, hours: int = Query(168, ge=1, le=87600),
    limit: int = Query(1000, ge=1, le=5000),
) -> dict:
    try:
        service = request.app.state.battery_service
        return {
            "voltage": service.voltage_history(device_id, hours=hours, limit=limit),
            "replacements": service.replacement_history(device_id),
        }
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post("/mark-charged", status_code=201)
def mark_charged(device_id: str, body: MarkChargedRequest, request: Request) -> dict:
    try:
        cycle = request.app.state.battery_service.mark_charged(device_id, **body.model_dump())
        return {"status": "recorded", "cycle_id": cycle.id, "cycle_number": cycle.cycle_number}
    except LookupError as exc:
        raise _not_found(exc) from exc


@router.post("/replace", status_code=201)
def replace(device_id: str, body: ReplaceBatteryRequest, request: Request) -> dict:
    try:
        event = request.app.state.battery_service.replace(device_id, **body.model_dump())
        return {"status": "recorded", "replacement_event_id": event.id}
    except LookupError as exc:
        raise _not_found(exc) from exc

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from gateway.app.database import get_session
from gateway.app.models import (
    RegisteredDevice,
    VibrationBaseline,
    VibrationBaselineHistory,
    VibrationCondition,
    VibrationWindow,
)
from gateway.app.services.vibration_condition import VibrationConditionService, public_statistics

router = APIRouter(prefix="/api/devices", tags=["vibration"])


class BaselineResetRequest(BaseModel):
    confirmation: str = Field(pattern="^RESET BASELINE$")


class BaselineRelearnRequest(BaseModel):
    confirmation: str = Field(pattern="^RELEARN BASELINE$")
    reason: str | None = Field(default=None, max_length=240)
    request_id: str = Field(min_length=8, max_length=64, pattern="^[A-Za-z0-9_-]+$")


def _service(request: Request) -> VibrationConditionService:
    settings = request.app.state.settings
    return VibrationConditionService(
        request.app.state.session_factory, request.app.state.gateway_id,
        minimum_windows=settings.vibration_baseline_minimum_windows,
        persistence_windows=settings.vibration_condition_persistence_windows,
        persistence_interval_seconds=settings.vibration_persistence_interval_seconds,
    )


def _device(session: Session, device_id: str) -> RegisteredDevice:
    device = session.scalar(select(RegisteredDevice).where(RegisteredDevice.device_id == device_id))
    if device is None or device.archived:
        raise HTTPException(status_code=404, detail="device not found")
    return device


def _window(item: VibrationWindow) -> dict:
    fields = {column.name: getattr(item, column.name) for column in VibrationWindow.__table__.columns}
    observed_at = item.observed_at.replace(tzinfo=UTC) if item.observed_at.tzinfo is None else item.observed_at.astimezone(UTC)
    fields["observed_at"] = observed_at.isoformat().replace("+00:00", "Z")
    return fields


@router.get("/{device_id}/vibration/latest")
def latest(device_id: str, request: Request, session: Session = Depends(get_session)) -> dict:
    device = _device(session, device_id)
    item = session.scalar(select(VibrationWindow).where(
        VibrationWindow.registered_device_id == device.id).order_by(desc(VibrationWindow.observed_at)).limit(1))
    if item is None:
        raise HTTPException(status_code=404, detail="no vibration history")
    result = _window(item)
    observed_at = item.observed_at.replace(tzinfo=UTC) if item.observed_at.tzinfo is None else item.observed_at.astimezone(UTC)
    age_seconds = max(0.0, (datetime.now(UTC) - observed_at).total_seconds())
    result.update(age_seconds=age_seconds, stale=age_seconds > request.app.state.settings.stale_after_seconds)
    return result


@router.get("/{device_id}/vibration/history")
def history(device_id: str, request: Request, session: Session = Depends(get_session),
            limit: int = Query(100, ge=1), start: datetime | None = None, end: datetime | None = None) -> dict:
    settings = request.app.state.settings
    if limit > settings.history_page_size_max:
        raise HTTPException(status_code=422, detail=f"limit must not exceed {settings.history_page_size_max}")
    device = _device(session, device_id)
    end = end or datetime.now(UTC)
    start = start or end - timedelta(days=1)
    end = end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)
    start = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
    if end <= start or end - start > timedelta(days=settings.history_max_days):
        raise HTTPException(status_code=422, detail="invalid or excessive history range")
    rows = list(session.scalars(select(VibrationWindow).where(
        VibrationWindow.registered_device_id == device.id,
        VibrationWindow.observed_at >= start, VibrationWindow.observed_at <= end,
    ).order_by(desc(VibrationWindow.observed_at)).limit(limit)))
    return {"items": [_window(item) for item in rows], "limit": limit}


@router.get("/{device_id}/vibration/baseline")
def baseline(device_id: str, session: Session = Depends(get_session)) -> dict:
    device = _device(session, device_id)
    item = session.scalar(select(VibrationBaseline).where(
        VibrationBaseline.registered_device_id == device.id).order_by(desc(VibrationBaseline.updated_at)).limit(1))
    if item is None:
        return {"status": "not_started", "sample_count": 0}
    raw = json.loads(item.statistics_json)
    return {
        "baseline_version": item.baseline_version, "algorithm_version": item.algorithm_version,
        "status": item.status, "sample_count": item.sample_count, "minimum_samples": item.minimum_samples,
        "installation_id": item.installation_id, "created_at": item.created_at,
        "established_at": item.established_at, "reason": item.reason, "statistics": public_statistics(raw),
    }


@router.get("/{device_id}/vibration/baseline/history")
def baseline_history(device_id: str, session: Session = Depends(get_session),
                     limit: int = Query(20, ge=1, le=100)) -> dict:
    device = _device(session, device_id)
    active = session.scalar(select(VibrationBaseline).where(
        VibrationBaseline.registered_device_id == device.id).order_by(desc(VibrationBaseline.updated_at)).limit(1))
    prior = list(session.scalars(select(VibrationBaselineHistory).where(
        VibrationBaselineHistory.registered_device_id == device.id
    ).order_by(desc(VibrationBaselineHistory.baseline_version)).limit(limit)))
    items = []
    if active:
        items.append({
            "baseline_version": active.baseline_version, "algorithm_version": active.algorithm_version,
            "status": active.status, "sample_count": active.sample_count, "minimum_samples": active.minimum_samples,
            "installation_id": active.installation_id, "created_at": active.created_at,
            "established_at": active.established_at, "superseded_at": None, "reason": active.reason,
        })
    items.extend({
        "baseline_version": item.baseline_version, "algorithm_version": item.algorithm_version,
        "status": item.status, "sample_count": item.sample_count, "minimum_samples": item.minimum_samples,
        "installation_id": item.installation_id, "created_at": item.created_at,
        "established_at": item.established_at, "superseded_at": item.superseded_at, "reason": item.reason,
    } for item in prior[:max(0, limit - len(items))])
    return {"items": items, "limit": limit}


@router.get("/{device_id}/condition")
def condition(device_id: str, session: Session = Depends(get_session)) -> dict:
    device = _device(session, device_id)
    item = session.scalar(select(VibrationCondition).where(VibrationCondition.registered_device_id == device.id))
    if item is None:
        return {"state": "INSUFFICIENT_DATA", "baseline_similarity_score": None, "factors": []}
    return {
        "state": item.state, "baseline_similarity_score": item.baseline_similarity_score,
        "factors": json.loads(item.factors_json), "latest_window_sequence": item.latest_window_sequence,
        "evaluated_at": item.evaluated_at, "installation_id": item.installation_id,
        "pending_state": item.pending_state, "pending_count": item.pending_count,
    }


@router.post("/{device_id}/vibration/baseline/reset", status_code=204)
def reset_baseline(device_id: str, _body: BaselineResetRequest, request: Request,
                   session: Session = Depends(get_session)) -> None:
    _device(session, device_id)
    try:
        _service(request).reset_baseline(device_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{device_id}/vibration/baseline/relearn")
def relearn_baseline(device_id: str, body: BaselineRelearnRequest, request: Request,
                     session: Session = Depends(get_session)) -> dict:
    _device(session, device_id)
    try:
        return _service(request).relearn_baseline(
            device_id, reason=body.reason.strip() if body.reason and body.reason.strip() else None,
            request_id=body.request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

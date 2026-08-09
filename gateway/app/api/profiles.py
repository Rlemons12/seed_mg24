import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from gateway.app.database import get_session
from gateway.app.models import AuditEvent
from gateway.app.profiles.models import SensorProfile
from gateway.app.profiles.registry import DuplicateProfileError
from gateway.app.schemas import ProfileValidationRequest

router = APIRouter(prefix="/api/sensor-profiles", tags=["sensor profiles"])


@router.get("", response_model=list[SensorProfile])
def list_profiles(
    request: Request,
    manufacturer: str | None = None,
    model: str | None = None,
    category: str | None = None,
    interface_type: str | None = None,
    include_disabled: bool = False,
) -> list[SensorProfile]:
    return request.app.state.profile_registry.list(
        manufacturer=manufacturer, model=model, category=category, interface_type=interface_type, include_disabled=include_disabled
    )


@router.get("/errors")
def profile_errors(request: Request) -> dict:
    return {"items": [error.__dict__ for error in request.app.state.profile_registry.errors]}


@router.post("/validate")
def validate_profile(body: ProfileValidationRequest) -> dict:
    try:
        profile = SensorProfile.model_validate(body.profile)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
    return {"valid": True, "profile": profile.model_dump(mode="json")}


@router.post("/reload")
def reload_profiles(request: Request) -> dict:
    request.app.state.profile_registry.reload()
    return {
        "loaded": len(request.app.state.profile_registry.list(include_disabled=True)),
        "errors": [error.__dict__ for error in request.app.state.profile_registry.errors],
    }


@router.post("/import", status_code=status.HTTP_201_CREATED, response_model=SensorProfile)
async def import_profile(request: Request, session: Session = Depends(get_session)) -> SensorProfile:
    content_length = request.headers.get("content-length")
    maximum = request.app.state.settings.max_profile_upload_bytes
    if content_length and int(content_length) > maximum:
        raise HTTPException(status_code=413, detail="profile upload exceeds configured maximum")
    data = await request.body()
    try:
        profile = request.app.state.profile_registry.import_profile(data)
    except DuplicateProfileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.add(
        AuditEvent(
            event_type="sensor_profile_imported",
            subject_id=profile.profile_id,
            detail_json=json.dumps(
                {"version": profile.profile_version, "status": profile.status, "source": profile.provenance.source}, separators=(",", ":")
            ),
        )
    )
    session.commit()
    return profile


@router.get("/{profile_id}", response_model=SensorProfile)
def get_profile(profile_id: str, request: Request, version: str | None = Query(default=None)) -> SensorProfile:
    profile = request.app.state.profile_registry.get(profile_id, version)
    if profile is None:
        raise HTTPException(status_code=404, detail="sensor profile not found")
    return profile

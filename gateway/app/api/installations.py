import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from gateway.app.database import get_session
from gateway.app.models import ProvisioningAttempt, Reading
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.repositories.installation_repository import DuplicateInstallationError, InstallationRepository
from gateway.app.schemas import InstallationCreate, InstallationResponse, InstallationUpdate, ProfileUpgradeRequest
from gateway.app.services.channel_configuration_service import DefaultChannelConfigurationService
from gateway.app.services.installation_service import InstallationValidationError, SensorInstallationService
from gateway.app.services.node_capability_service import NodeCapabilityService
from gateway.app.services.provisioning_service import ProvisioningError

router = APIRouter(prefix="/api/sensor-installations", tags=["sensor installations"])


def response_for(installation) -> InstallationResponse:
    values = {column.name: getattr(installation, column.name) for column in installation.__table__.columns}
    values["configuration"] = json.loads(installation.configuration_json)
    return InstallationResponse.model_validate(values)


def service(request: Request, session: Session) -> SensorInstallationService:
    return SensorInstallationService(
        InstallationRepository(session),
        DeviceRepository(session),
        request.app.state.profile_registry,
        request.app.state.settings.device_id_pattern,
    )


@router.post("", response_model=InstallationResponse, status_code=status.HTTP_201_CREATED)
def create_installation(body: InstallationCreate, request: Request, session: Session = Depends(get_session)) -> InstallationResponse:
    try:
        return response_for(service(request, session).create_draft(body))
    except DuplicateInstallationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (InstallationValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=list[InstallationResponse])
def list_installations(session: Session = Depends(get_session)) -> list[InstallationResponse]:
    return [response_for(item) for item in InstallationRepository(session).list()]


@router.get("/{installation_id}", response_model=InstallationResponse)
def get_installation(installation_id: str, session: Session = Depends(get_session)) -> InstallationResponse:
    installation = InstallationRepository(session).get(installation_id)
    if installation is None or installation.archived:
        raise HTTPException(status_code=404, detail="installation not found")
    return response_for(installation)


@router.patch("/{installation_id}", response_model=InstallationResponse)
def update_installation(
    installation_id: str, body: InstallationUpdate, request: Request, session: Session = Depends(get_session)
) -> InstallationResponse:
    repository = InstallationRepository(session)
    installation = repository.get(installation_id)
    if installation is None or installation.archived:
        raise HTTPException(status_code=404, detail="installation not found")
    try:
        return response_for(service(request, session).update(installation, body))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{installation_id}/validate", response_model=InstallationResponse)
async def validate_installation(installation_id: str, request: Request) -> InstallationResponse:
    try:
        return response_for(await request.app.state.provisioning_service.validate(installation_id))
    except ProvisioningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{installation_id}/apply", response_model=InstallationResponse)
async def apply_installation(installation_id: str, request: Request) -> InstallationResponse:
    try:
        return response_for(await request.app.state.provisioning_service.apply(installation_id))
    except ProvisioningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{installation_id}/verify", response_model=InstallationResponse)
async def verify_installation(installation_id: str, request: Request) -> InstallationResponse:
    try:
        return response_for(await request.app.state.provisioning_service.verify(installation_id))
    except ProvisioningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{installation_id}/disable", response_model=InstallationResponse)
def disable_installation(installation_id: str, session: Session = Depends(get_session)) -> InstallationResponse:
    repository = InstallationRepository(session)
    installation = repository.get(installation_id)
    if installation is None:
        raise HTTPException(status_code=404, detail="installation not found")
    return response_for(repository.update(installation, enabled=False, provisioning_state="disabled"))


@router.post("/{installation_id}/upgrade-profile", response_model=InstallationResponse)
def upgrade_profile(
    installation_id: str, body: ProfileUpgradeRequest, request: Request, session: Session = Depends(get_session)
) -> InstallationResponse:
    repository = InstallationRepository(session)
    installation = repository.get(installation_id)
    if installation is None:
        raise HTTPException(status_code=404, detail="installation not found")
    profile = request.app.state.profile_registry.get(installation.sensor_profile_id, body.profile_version)
    if profile is None:
        raise HTTPException(status_code=404, detail="requested profile version not found")
    if body.profile_version == installation.sensor_profile_version:
        return response_for(installation)
    try:
        configuration = SensorInstallationService.configuration(installation)
        capabilities = NodeCapabilityService(DeviceRepository(session)).get(installation.node_id)
        DefaultChannelConfigurationService().validate(profile, capabilities, installation.interface_id, configuration)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    previous = installation.configuration_json
    updated = repository.update(
        installation,
        sensor_profile_version=body.profile_version,
        previous_configuration_json=previous,
        provisioning_state="ready_to_apply",
        verification_status="pending",
        enabled=False,
    )
    return response_for(updated)


@router.get("/{installation_id}/history")
def provisioning_history(installation_id: str, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(
        select(ProvisioningAttempt)
        .where(ProvisioningAttempt.installation_id == installation_id)
        .order_by(ProvisioningAttempt.created_at.desc())
    ).all()
    return [
        {
            "transaction_id": row.transaction_id,
            "state": row.state,
            "error": row.error,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


@router.get("/{installation_id}/preview")
def preview_installation(installation_id: str, session: Session = Depends(get_session)) -> dict:
    installation = InstallationRepository(session).get(installation_id)
    if installation is None or installation.archived:
        raise HTTPException(status_code=404, detail="installation not found")
    node = DeviceRepository(session).get(installation.node_id)
    capabilities = NodeCapabilityService(DeviceRepository(session)).get(installation.node_id)
    interface = next(item for item in capabilities.interfaces if item.interface_id == installation.interface_id)
    rows = list(
        session.scalars(
            select(Reading)
            .where(Reading.registered_device_id == node.id, Reading.channel.in_(interface.telemetry_channels))
            .order_by(Reading.received_at.desc())
            .limit(50)
        )
    )
    latest_by_channel = {}
    for row in rows:
        latest_by_channel.setdefault(row.channel, row)
    values = [row.normalized_value for row in rows[:5] if row.normalized_value is not None]
    stale = (
        not rows
        or (
            datetime.now(UTC) - (rows[0].received_at.replace(tzinfo=UTC) if rows[0].received_at.tzinfo is None else rows[0].received_at)
        ).total_seconds()
        > 30
    )
    return {
        "installation_id": installation_id,
        "updating": len(rows) >= 2 and not stale,
        "stale": stale,
        "constant": len(values) >= 5 and len(set(values)) == 1,
        "calibration_status": installation.calibration_status,
        "channels": [
            {
                "channel": row.channel,
                "raw_value": row.raw_value,
                "value": row.normalized_value,
                "unit": row.unit,
                "quality": row.quality,
                "received_at": row.received_at,
            }
            for row in latest_by_channel.values()
        ],
    }


@router.delete("/{installation_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_installation(
    installation_id: str, confirm_hard_delete: bool = Query(False), session: Session = Depends(get_session)
) -> Response:
    repository = InstallationRepository(session)
    installation = repository.get(installation_id)
    if installation is None:
        raise HTTPException(status_code=404, detail="installation not found")
    if confirm_hard_delete:
        raise HTTPException(status_code=409, detail="hard deletion is not supported; disable/archive preserves history")
    repository.update(installation, archived=True, enabled=False, provisioning_state="disabled")
    return Response(status_code=204)

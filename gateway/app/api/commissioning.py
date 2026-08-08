from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.schemas import DeviceCreate, DeviceResponse, InstallationConfiguration
from gateway.app.services.ble_provisioning import BleProvisioningError
from gateway.app.services.device_service import DeviceService

router = APIRouter(prefix="/api/commissioning", tags=["commissioning"])


class CommissionNodeRequest(BaseModel):
    discovery_address: str = Field(min_length=3, max_length=128)
    node_id: str = Field(pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$", max_length=31)
    display_name: str = Field(min_length=1, max_length=160)
    location: str | None = Field(default=None, max_length=240)
    configuration: InstallationConfiguration
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{16,24}$")


@router.post("/nodes", response_model=DeviceResponse)
async def commission_node(body: CommissionNodeRequest, request: Request) -> DeviceResponse:
    discovery = request.app.state.scanner.get(body.discovery_address)
    if discovery is None or not discovery.compatible:
        raise HTTPException(status_code=409, detail="selected compatible discovery expired; scan again")
    with request.app.state.session_factory() as session:
        existing = DeviceRepository(session).get(body.node_id)
        if existing is not None:
            if existing.ble_address == body.discovery_address:
                return DeviceResponse.model_validate(existing)
            raise HTTPException(status_code=409, detail="node_id is already registered")
    try:
        result = await request.app.state.node_provisioner.provision(
            body.discovery_address, body.node_id, body.idempotency_key, body.configuration.model_dump()
        )
    except (BleProvisioningError, TimeoutError, ConnectionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    with request.app.state.session_factory() as session:
        service = DeviceService(DeviceRepository(session), request.app.state.settings.device_id_pattern)
        device = service.register(
            DeviceCreate(device_id=body.node_id, display_name=body.display_name, discovery_address=body.discovery_address,
                         location=body.location),
            discovery.model_copy(update={"stable_device_id": body.node_id}),
        )
        metadata = result["metadata"]
        device = DeviceRepository(session).update(
            device, firmware_version=metadata.get("firmware_version"), sensor_package_version=metadata.get("sensor_package_version"),
            protocol_version=metadata.get("protocol_version"), configuration_schema_version=metadata.get("configuration_schema_version"),
            build_identifier=metadata.get("build_identifier"), firmware_git_commit=metadata.get("git_commit"),
            compatibility_status="compatible", compatibility_message="Provisioning readback verified",
        )
    request.app.state.ble_manager.schedule(device.device_id, device.ble_address)
    return DeviceResponse.model_validate(device).model_copy(update={"connection_status": "connecting"})

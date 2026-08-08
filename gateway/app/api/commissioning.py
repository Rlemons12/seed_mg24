from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.schemas import DeviceCreate, DeviceResponse, InstallationConfiguration
from gateway.app.services.ble_provisioning import BleProvisioningError
from gateway.app.services.device_service import DeviceService

router = APIRouter(prefix="/api/commissioning", tags=["commissioning"])


def assigned_conflict(node_id: str) -> dict:
    return {
        "code": "device_already_assigned",
        "message": f"This MG24 is already assigned as {node_id}; application firmware installation preserves identity.",
        "assigned_node_id": node_id,
        "recovery": "Use its original dashboard database or the documented USB application-factory recovery workflow.",
    }


class CommissionNodeRequest(BaseModel):
    discovery_address: str = Field(min_length=3, max_length=128)
    node_id: str = Field(pattern=r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$", max_length=31)
    display_name: str = Field(min_length=1, max_length=160)
    location: str | None = Field(default=None, max_length=240)
    configuration: InstallationConfiguration
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{16,24}$")


@router.get("/discoveries")
async def commissioning_discoveries(request: Request) -> list[dict]:
    results = []
    for discovery in request.app.state.scanner.discoveries():
        item = discovery.model_dump(mode="json")
        if not discovery.compatible:
            item.update({"commissioning_state": "incompatible", "action": "diagnose",
                         "commissioning_eligible": False, "allowed_actions": ["diagnose", "rescan", "close"]})
            results.append(item)
            continue
        with request.app.state.session_factory() as session:
            local = DeviceRepository(session).get_by_ble_address(discovery.address)
        if local is not None:
            item.update({"stable_device_id": local.device_id, "temporary_id": None,
                         "reported_node_id": local.device_id, "assigned_node_id": local.device_id,
                         "commissioning_state": "registered_here", "commissioning_eligible": False,
                         "action": "view_or_reconnect", "allowed_actions": ["view", "reconnect", "rescan", "close"],
                         "local_device_id": local.device_id})
            results.append(item)
            continue
        try:
            state = await request.app.state.node_provisioner.read_state(discovery.address)
            assigned_id = state["readback"]["id"]
        except (BleProvisioningError, TimeoutError, ConnectionError) as exc:
            item.update({"commissioning_state": "state_unavailable", "commissioning_eligible": False,
                         "action": "retry_scan", "allowed_actions": ["rescan", "close"], "message": str(exc)})
            results.append(item)
            continue
        item["reported_node_id"] = assigned_id
        if assigned_id == "UNASSIGNED-MG24":
            item.update({"commissioning_state": "unassigned", "commissioning_eligible": True,
                         "action": "commission", "allowed_actions": ["commission", "rescan", "close"]})
        else:
            item.update({"stable_device_id": assigned_id, "temporary_id": None,
                         "assigned_node_id": assigned_id, "commissioning_state": "assigned_elsewhere",
                         "commissioning_eligible": False, "action": "recovery_or_import",
                         "allowed_actions": ["view_assignment", "recovery_instructions", "rescan", "close"],
                         "message": assigned_conflict(assigned_id)["message"]})
        results.append(item)
    return results


@router.post("/nodes", response_model=DeviceResponse)
async def commission_node(body: CommissionNodeRequest, request: Request) -> DeviceResponse:
    discovery = request.app.state.scanner.get(body.discovery_address)
    if discovery is None or not discovery.compatible:
        raise HTTPException(status_code=409, detail="selected compatible discovery expired; scan again")
    with request.app.state.session_factory() as session:
        repository = DeviceRepository(session)
        local_by_address = repository.get_by_ble_address(body.discovery_address)
        if local_by_address is not None:
            raise HTTPException(status_code=409, detail={
                "code": "device_already_registered",
                "message": f"This MG24 is already registered here as {local_by_address.device_id}.",
                "assigned_node_id": local_by_address.device_id,
                "action": "view_or_reconnect",
            })
        existing = repository.get(body.node_id)
        if existing is not None:
            if existing.ble_address == body.discovery_address:
                return DeviceResponse.model_validate(existing)
            raise HTTPException(status_code=409, detail="node_id is already registered")
    try:
        state = await request.app.state.node_provisioner.read_state(body.discovery_address)
    except (BleProvisioningError, TimeoutError, ConnectionError) as exc:
        raise HTTPException(status_code=409, detail={"code": "device_state_unavailable", "message": str(exc)}) from exc
    assigned_id = state["readback"]["id"]
    if assigned_id != "UNASSIGNED-MG24":
        with request.app.state.session_factory() as session:
            local = DeviceRepository(session).get_by_ble_address(body.discovery_address)
            if local is not None and local.device_id == assigned_id:
                return DeviceResponse.model_validate(local)
        raise HTTPException(status_code=409, detail=assigned_conflict(assigned_id))
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

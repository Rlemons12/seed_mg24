from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy.orm import Session

from gateway.app.database import get_session
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.repositories.firmware_repository import FirmwareHistoryRepository
from gateway.app.repositories.installation_repository import InstallationRepository
from gateway.app.schemas import InstallationConfiguration, NodeCapabilities
from gateway.app.services.ble_provisioning import BleProvisioningError
from gateway.app.services.node_capability_service import NodeCapabilityService, NodeNotFoundError

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


class DeviceConfigurationRequest(InstallationConfiguration):
    transaction_id: str = Field(pattern=r"^[0-9a-f]{16,24}$")


@router.get("")
def list_nodes(request: Request, session: Session = Depends(get_session)) -> list[dict]:
    repository = DeviceRepository(session)
    return [
        {
            "node_id": node.device_id,
            "display_name": node.display_name,
            "ble_address": node.ble_address,
            "ble_advertised_name": node.ble_advertised_name,
            "hardware_id": node.hardware_id,
            "firmware_version": node.firmware_version,
            "sensor_package_version": node.sensor_package_version,
            "protocol_version": node.protocol_version,
            "compatibility_status": node.compatibility_status,
            "compatibility_message": node.compatibility_message,
            **request.app.state.ble_manager.runtime(node.device_id, node.last_seen_at),
        }
        for node in repository.list()
    ]


@router.get("/{node_id}/firmware-history")
def firmware_history(node_id: str, session: Session = Depends(get_session)) -> list[dict]:
    if not DeviceRepository(session).get(node_id):
        raise HTTPException(status_code=404, detail="node not found")
    return [
        {column.name: getattr(row, column.name) for column in row.__table__.columns}
        for row in FirmwareHistoryRepository(session).list(node_id)
    ]


@router.get("/{node_id}/capabilities", response_model=NodeCapabilities)
def capabilities(node_id: str, request: Request, session: Session = Depends(get_session)) -> NodeCapabilities:
    try:
        connection = request.app.state.ble_manager.connections.get(node_id)
        reported = connection.capabilities if connection else None
        return NodeCapabilityService(DeviceRepository(session)).get(node_id, reported)
    except NodeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{node_id}/interfaces")
def interfaces(node_id: str, request: Request, session: Session = Depends(get_session)) -> list[dict]:
    try:
        connection = request.app.state.ble_manager.connections.get(node_id)
        reported = connection.capabilities if connection else None
        result = NodeCapabilityService(DeviceRepository(session)).get(node_id, reported)
    except NodeNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    installations = InstallationRepository(session)
    rows = []
    for item in result.interfaces:
        occupied = installations.occupied_interface(node_id, item.interface_id)
        rows.append(
            {
                **item.model_dump(mode="json"),
                "available": not item.exclusive or occupied is None,
                "assigned_device_id": occupied.device_id if occupied else None,
            }
        )
    return rows


@router.get("/{node_id}/configuration")
async def read_device_configuration(node_id: str, request: Request, session: Session = Depends(get_session)) -> dict:
    device = DeviceRepository(session).get(node_id)
    if device is None or not device.ble_address:
        raise HTTPException(status_code=404, detail="node or BLE address not found")
    await request.app.state.ble_manager.remove(node_id)
    try:
        state = await request.app.state.node_provisioner.read_state(device.ble_address)
    finally:
        request.app.state.ble_manager.schedule(node_id, device.ble_address)
    readback = state["readback"]
    return {
        "scope": "device",
        "node_id": state["readback"]["id"],
        "metadata_node_id": state["metadata"].get("node_id"),
        "capabilities_node_id": state["capabilities"].get("node_id"),
        "firmware_version": state["metadata"].get("firmware_version"),
        "protocol_version": state["metadata"].get("protocol_version"),
        "affects": ["microphone processing", "device reporting cadence"],
        "telemetry_only_inputs": ["IMU0", "VBAT", "D0", "D1", "D2", "D3", "D4", "D5"],
        "sample_interval_ms": readback["sample"], "processing_interval_ms": readback["process"],
        "report_interval_ms": readback["report"], "heartbeat_interval_ms": readback["heartbeat"],
        "filter_type": next(
            (name for name, value in request.app.state.node_provisioner.FILTERS.items() if value == readback["filter"]),
            "none",
        ),
        "filter_window": readback["window"], "enabled": bool(readback["enabled"]),
        "generation": readback.get("generation"),
    }


@router.post("/{node_id}/configuration")
async def configure_device(node_id: str, body: DeviceConfigurationRequest, request: Request,
                           session: Session = Depends(get_session)) -> dict:
    device = DeviceRepository(session).get(node_id)
    if device is None or not device.ble_address:
        raise HTTPException(status_code=404, detail="node or BLE address not found")
    operation_key = (node_id, body.transaction_id)
    cached = request.app.state.device_configuration_results.get(operation_key)
    if cached is not None:
        return cached
    configuration = body.model_dump(exclude={"transaction_id"}) | {"enabled": True}
    await request.app.state.ble_manager.remove(node_id)
    try:
        result = await request.app.state.node_provisioner.configure(
            device.ble_address, node_id, body.transaction_id, configuration
        )
    except (BleProvisioningError, TimeoutError, ConnectionError) as exc:
        raise HTTPException(status_code=409, detail={
            "code": "configuration_not_verified",
            "message": "The sensor did not confirm the change. Nothing is shown as successful until readback agrees.",
            "changed": "unknown", "next_action": "authoritative_readback", "technical_detail": str(exc),
        }) from exc
    finally:
        request.app.state.ble_manager.schedule(node_id, device.ble_address)
    response = {"status": "verified", "scope": "device", "transaction_id": body.transaction_id,
                "readback": result["readback"], "acknowledgement": result["acknowledgement"]}
    request.app.state.device_configuration_results[operation_key] = response
    return response

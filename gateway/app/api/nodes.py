from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from gateway.app.database import get_session
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.repositories.firmware_repository import FirmwareHistoryRepository
from gateway.app.repositories.installation_repository import InstallationRepository
from gateway.app.schemas import NodeCapabilities
from gateway.app.services.node_capability_service import NodeCapabilityService, NodeNotFoundError

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


@router.get("")
def list_nodes(request: Request, session: Session = Depends(get_session)) -> list[dict]:
    repository = DeviceRepository(session)
    return [
        {
            "node_id": node.device_id,
            "display_name": node.display_name,
            "ble_address": node.ble_address,
            "ble_advertised_name": node.ble_advertised_name,
            "firmware_version": node.firmware_version,
            "sensor_package_version": node.sensor_package_version,
            "protocol_version": node.protocol_version,
            "compatibility_status": node.compatibility_status,
            "compatibility_message": node.compatibility_message,
            **request.app.state.ble_manager.runtime(node.device_id),
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

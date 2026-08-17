from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from gateway.app.services.firmware_installation import FirmwareValidationError

router = APIRouter(prefix="/api/firmware", tags=["local firmware installation"])


def require_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="firmware management is available only from the local dashboard host")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).hostname != request.url.hostname:
        raise HTTPException(status_code=403, detail="cross-origin firmware management is not permitted")


class InstallRequest(BaseModel):
    hardware_serial: str = Field(pattern=r"^[A-F0-9]{8,32}$")
    package_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")


class DeveloperApprovalRequest(InstallRequest):
    confirmation: str


@router.get("/packages")
def packages(request: Request) -> list[dict]:
    require_loopback(request)
    return request.app.state.firmware_catalog.list()


@router.get("/boards")
def boards(request: Request) -> list[dict]:
    require_loopback(request)
    return request.app.state.firmware_installer.boards()


@router.post("/developer-approve")
def developer_approve(body: DeveloperApprovalRequest, request: Request) -> dict:
    require_loopback(request)
    if body.confirmation != "APPROVE DEVELOPMENT FIRMWARE":
        raise HTTPException(status_code=422, detail="explicit development firmware confirmation is required")
    matches = [
        board for board in request.app.state.firmware_installer.boards()
        if board["hardware_serial"] == body.hardware_serial
    ]
    if len(matches) != 1:
        raise HTTPException(status_code=409, detail="exactly one matching supported board is required")
    try:
        return request.app.state.firmware_catalog.approve_development(body.package_id)
    except FirmwareValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/install", status_code=202)
async def install(body: InstallRequest, request: Request) -> dict:
    require_loopback(request)
    try:
        return vars(request.app.state.firmware_installer.start(body.hardware_serial, body.package_id))
    except FirmwareValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/operations/{operation_id}")
def operation(operation_id: str, request: Request) -> dict:
    require_loopback(request)
    item = request.app.state.firmware_installer.operations.get(operation_id)
    if item is None:
        raise HTTPException(status_code=404, detail="firmware operation not found")
    return vars(item)

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gateway import __version__
from gateway.app.api import commands, commissioning, device_lifecycle, devices, firmware, health, installations, nodes, profiles, telemetry
from gateway.app.ble.manager import BleManager
from gateway.app.ble.scanner import BleScannerService
from gateway.app.config import Settings, get_settings
from gateway.app.database import (
    create_database_engine,
    create_session_factory,
    get_session,
    initialize_database,
    session_dependency,
)
from gateway.app.instance_lock import GatewayInstanceLock
from gateway.app.logging_config import configure_logging
from gateway.app.models import utc_now
from gateway.app.profiles.registry import ProfileRegistry
from gateway.app.repositories.device_repository import DeviceRepository
from gateway.app.repositories.firmware_repository import FirmwareHistoryRepository
from gateway.app.services.ble_provisioning import BleNodeProvisioner
from gateway.app.services.compatibility_service import CompatibilityService
from gateway.app.services.firmware_installation import ApprovedFirmwareCatalog, UsbFirmwareInstaller
from gateway.app.services.lifecycle_confirmation import LifecycleConfirmationStore
from gateway.app.services.provisioning_service import BlePersistentConfigurator, PiAuthoritativeConfigurator, SensorProvisioningService
from gateway.app.services.telemetry_service import TelemetryService
from gateway.app.services.websocket_manager import WebSocketManager

PACKAGE_DIR = Path(__file__).parent
REPOSITORY_DIR = PACKAGE_DIR.parents[1]
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


def create_app(settings: Settings | None = None, *, client_factory=None, scanner_factory=None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    initialize_database(engine)
    profile_registry = ProfileRegistry(
        settings.sensor_profile_directory, REPOSITORY_DIR / "sensor_package" / "profiles" / "built_in", settings.max_profile_upload_bytes
    )
    profile_registry.reload()
    compatibility = CompatibilityService(REPOSITORY_DIR / "shared_protocol" / "compatibility.json")
    websocket_manager = WebSocketManager()
    telemetry_service = TelemetryService(session_factory, websocket_manager, settings.max_payload_bytes, settings.max_payload_json_bytes)
    catalog_path = settings.firmware_catalog_path
    if not catalog_path.is_absolute():
        catalog_path = REPOSITORY_DIR / catalog_path
    firmware_catalog = ApprovedFirmwareCatalog(REPOSITORY_DIR, catalog_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        instance_lock = GatewayInstanceLock(settings.database_url, settings.port)
        if settings.gateway_instance_lock:
            instance_lock.acquire()
        async def status_callback(device_id: str, state: str, error: str | None) -> None:
            with session_factory() as session:
                repository = DeviceRepository(session)
                device = repository.get(device_id)
                if device and not device.archived and device.enabled and device.lifecycle_state != "removed":
                    if state == "connected":
                        connection = manager.connections.get(device_id)
                        metadata = connection.metadata if connection else {}
                        result = compatibility.evaluate(metadata)
                        repository.update(
                            device,
                            firmware_version=metadata.get("firmware_version", metadata.get("fw", device.firmware_version)),
                            telemetry_schema_version=metadata.get("v", device.telemetry_schema_version),
                            sensor_package_version=metadata.get("sensor_package_version"),
                            protocol_version=metadata.get("protocol_version"),
                            configuration_schema_version=metadata.get("configuration_schema_version"),
                            build_identifier=metadata.get("build_identifier"),
                            firmware_git_commit=metadata.get("git_commit"),
                            compatibility_status=result.status,
                            compatibility_message=result.message,
                        )
                        if all(metadata.get(key) not in (None, "") for key in FirmwareHistoryRepository.TRACKED[:-1]):
                            FirmwareHistoryRepository(session).record(device_id, metadata, result.status)
                        repository.update_runtime(device, status=state, last_connected_at=utc_now())
                    else:
                        repository.update_runtime(device, status=state)
            await websocket_manager.broadcast("device_status", device_id, {"connection_status": state, "last_error": error})

        manager = BleManager(settings, telemetry_service.ingest, status_callback, client_factory=client_factory)
        app.state.ble_manager = manager
        with session_factory() as session:
            for device in DeviceRepository(session).list():
                if device.enabled and device.ble_address:
                    manager.schedule(device.device_id, device.ble_address)
        try:
            yield
        finally:
            await manager.shutdown()
            engine.dispose()
            if settings.gateway_instance_lock:
                instance_lock.release()

    app = FastAPI(title="Seed Sensor Gateway", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def protect_lifecycle_requests(request: Request, call_next):
        if request.url.path in {"/api/device-lifecycle/confirm", "/api/device-lifecycle/execute"}:
            if request.method != "POST":
                return JSONResponse(status_code=405, content={"error": "method_not_allowed", "detail": "POST is required."})
            content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                return JSONResponse(status_code=415, content={"error": "json_required", "detail": "application/json is required."})
            origin = request.headers.get("origin")
            if not origin and (not request.client or request.client.host != "testclient"):
                return JSONResponse(status_code=403, content={"error": "same_origin_required", "detail": "Origin is required."})
            if origin and urlsplit(origin).hostname != request.url.hostname:
                return JSONResponse(status_code=403, content={"error": "same_origin_required", "detail": "Cross-origin request denied."})
        return await call_next(request)

    @app.middleware("http")
    async def prevent_stale_dashboard_assets(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response
    app.state.version = __version__
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.websocket_manager = websocket_manager
    app.state.profile_registry = profile_registry
    app.state.node_provisioner = BleNodeProvisioner(client_factory, settings.provisioning_timeout_seconds)
    app.state.device_configuration_results = {}
    app.state.lifecycle_confirmations = LifecycleConfirmationStore()
    configurator = (
        BlePersistentConfigurator(session_factory, app.state.node_provisioner, lambda: app.state.ble_manager)
        if client_factory is None
        else PiAuthoritativeConfigurator()
    )
    app.state.provisioning_service = SensorProvisioningService(
        session_factory, profile_registry, configurator=configurator,
        timeout_seconds=settings.provisioning_timeout_seconds,
    )
    app.state.firmware_catalog = firmware_catalog
    app.state.firmware_installer = UsbFirmwareInstaller(firmware_catalog, REPOSITORY_DIR)
    app.state.scanner = BleScannerService(settings.scan_duration_seconds, settings.discovery_ttl_seconds, scanner_factory)
    app.state.ble_manager = BleManager(settings, telemetry_service.ingest, lambda *_: None, client_factory=client_factory)
    app.dependency_overrides[get_session] = session_dependency(session_factory)
    app.include_router(health.router)
    app.include_router(devices.router)
    app.include_router(device_lifecycle.router)
    app.include_router(commands.router)
    app.include_router(telemetry.router)
    app.include_router(telemetry.ws_router)
    app.include_router(profiles.router)
    app.include_router(nodes.router)
    app.include_router(installations.router)
    app.include_router(commissioning.router)
    app.include_router(firmware.router)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"dashboard_build": f"{__version__}-module-shell-1", "current_module": "overview"},
        )

    @app.get("/installations", include_in_schema=False)
    def installations_page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="installations.html",
            context={"dashboard_build": f"{__version__}-module-shell-1", "current_module": "installations"},
        )

    @app.get("/system-health", include_in_schema=False)
    def system_health_page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="system_health.html",
            context={"dashboard_build": f"{__version__}-module-shell-1", "current_module": "system-health"},
        )

    @app.exception_handler(HTTPException)
    async def structured_http_error(request: Request, exc: HTTPException):
        response = await http_exception_handler(request, exc)
        return JSONResponse(status_code=response.status_code, content={"error": "request_error", "detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(part) for part in item["loc"] if part not in {"body", "query"}), "message": item["msg"]}
            for item in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"error": "validation_error", "detail": details})

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, _exc: Exception):
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": "an internal error occurred"})

    return app


app = create_app()

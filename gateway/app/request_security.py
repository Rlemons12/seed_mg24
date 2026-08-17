import ipaddress
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

MAX_DESTRUCTIVE_BODY_BYTES = 4096


def _effective_port(scheme: str, port: int | None) -> int | None:
    return port if port is not None else {"http": 80, "https": 443}.get(scheme)


def require_bounded_same_origin_json(request: Request) -> None:
    if request.method != "POST":
        raise HTTPException(status_code=405, detail={"code": "method_not_allowed", "message": "POST is required."})
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail={"code": "json_required", "message": "application/json is required."})
    length = request.headers.get("content-length")
    if length is None or not length.isdecimal() or int(length) > MAX_DESTRUCTIVE_BODY_BYTES:
        raise HTTPException(status_code=413, detail={"code": "body_too_large", "message": "A bounded request body is required."})
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if not origin or not host:
        raise HTTPException(status_code=403, detail={"code": "same_origin_required", "message": "Origin and Host are required."})
    try:
        parsed = urlsplit(origin)
        request_host = request.url.hostname
        same = (
            parsed.scheme in {"http", "https"}
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and parsed.scheme == request.url.scheme
            and parsed.hostname == request_host
            and _effective_port(parsed.scheme, parsed.port) == _effective_port(request.url.scheme, request.url.port)
        )
    except ValueError:
        same = False
    if not same:
        raise HTTPException(status_code=403, detail={"code": "same_origin_required", "message": "Cross-origin request denied."})


def require_loopback(request: Request) -> None:
    if any(request.headers.get(name) for name in ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip")):
        raise HTTPException(status_code=403, detail={"code": "loopback_required", "message": "Forwarded USB reset requests are denied."})
    peer = request.client.host if request.client else ""
    target = request.url.hostname or ""
    try:
        peer_loopback = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        peer_loopback = peer == "testclient"
    try:
        target_loopback = ipaddress.ip_address(target).is_loopback
    except ValueError:
        target_loopback = target in {"localhost", "testserver"}
    if not peer_loopback or not target_loopback:
        raise HTTPException(status_code=403, detail={"code": "loopback_required", "message": "USB reset is loopback-only."})

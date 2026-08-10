from __future__ import annotations

import hashlib
import json
import re
from typing import Any

PREFIX = "MG24BOOT1 "
SCHEMA_VERSION = 1
MAX_LINE_BYTES = 768
MAX_REQUEST_ID = 40
NODE_ID_PATTERN = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
HARDWARE_ID_PATTERN = re.compile(r"^0x[0-9A-F]{16}$")
ALLOWED_ACTIONS = {
    "read_node_state",
    "read_identity",
    "provision_identity",
    "read_configuration",
    "backup_state",
    "verify_state",
    "restore_configuration",
    "prepare_factory_reset",
    "confirm_factory_reset",
    "cancel_factory_reset",
}


class ProtocolError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def content_hash(value: dict[str, Any]) -> str:
    copy = dict(value)
    copy.pop("canonical_content_hash", None)
    return hashlib.sha256(canonical_json(copy)).hexdigest()


def encode_request(request_id: str, action: str, **fields: Any) -> bytes:
    if not request_id or len(request_id) > MAX_REQUEST_ID or not re.fullmatch(r"[A-Za-z0-9_-]+", request_id):
        raise ProtocolError("invalid request_id")
    if action not in ALLOWED_ACTIONS:
        raise ProtocolError("unknown action")
    if action == "confirm_factory_reset":
        aliases = {
            "reset_protocol_version": "rv", "scope": "s", "expected_hardware_id": "h",
            "operation_id": "op", "challenge": "c",
        }
        body = {"t": "bootstrap_request", "v": SCHEMA_VERSION, "id": request_id, "a": action}
        body.update({aliases.get(key, key): value for key, value in fields.items()})
    else:
        body = {
            "type": "bootstrap_request", "schema_version": SCHEMA_VERSION,
            "request_id": request_id, "action": action, **fields,
        }
    line = PREFIX.encode() + canonical_json(body) + b"\n"
    if len(line) > MAX_LINE_BYTES:
        raise ProtocolError("request exceeds line limit")
    return line


def decode_response(line: bytes, request_id: str, action: str) -> dict[str, Any]:
    if len(line) > MAX_LINE_BYTES or not line.startswith(PREFIX.encode()):
        raise ProtocolError("invalid response framing")
    try:
        body = json.loads(line[len(PREFIX) :])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("malformed response") from exc
    required = {"type", "schema_version", "request_id", "action", "status"}
    if not isinstance(body, dict) or not required <= body.keys():
        raise ProtocolError("incomplete response")
    if body["type"] != "bootstrap_response" or body["schema_version"] != SCHEMA_VERSION:
        raise ProtocolError("unsupported response")
    if body["request_id"] != request_id or body["action"] != action:
        raise ProtocolError("response correlation mismatch")
    if body["status"] not in {"success", "error"}:
        raise ProtocolError("invalid response status")
    return body


def validate_backup(backup: dict[str, Any]) -> None:
    required = {"backup_schema_version", "created_at_utc", "source_node_id", "identity_included", "state", "canonical_content_hash"}
    if not isinstance(backup, dict) or backup.get("backup_schema_version") != 1 or not required <= backup.keys():
        raise ProtocolError("invalid backup schema")
    if backup["source_node_id"] is not None and not NODE_ID_PATTERN.fullmatch(backup["source_node_id"]):
        raise ProtocolError("invalid backup node_id")
    if backup["canonical_content_hash"] != content_hash(backup):
        raise ProtocolError("backup hash mismatch")

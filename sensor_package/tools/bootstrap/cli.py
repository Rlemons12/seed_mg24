from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .protocol import HARDWARE_ID_PATTERN, NODE_ID_PATTERN, ProtocolError, content_hash, validate_backup
from .serial_client import BootstrapSerialClient


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="XIAO MG24 USB bootstrap tool")
    p.add_argument("command", choices=["list", "read", "provision", "backup", "restore", "verify", "factory-reset"])
    p.add_argument("--port")
    p.add_argument("--node-id")
    p.add_argument("--file", type=Path)
    p.add_argument("--scope", choices=["application_factory"], default="application_factory")
    p.add_argument("--hardware-id")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--include-identity", action="store_true")
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument("--reboot-timeout", type=float, default=30.0)
    return p


def supported_ports() -> list[str]:
    from serial.tools import list_ports

    return [item.device for item in list_ports.comports() if item.vid == 0x2886 and item.pid == 0x0062]


def inspect_port(port: str, timeout: float) -> dict[str, Any]:
    with BootstrapSerialClient(port, timeout=timeout) as client:
        state = client.request("read_node_state")["result"]
    return {"port": port, **state}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "list":
            devices = []
            for port in supported_ports():
                try:
                    devices.append(inspect_port(port, args.timeout))
                except (OSError, ValueError, ProtocolError, RuntimeError) as exc:
                    devices.append({"port": port, "status": "unreadable", "error": str(exc)})
            print(json.dumps({"status": "success", "count": len(devices), "devices": devices}, indent=2))
            return 0
        if not args.port:
            raise ProtocolError("explicit --port is required")
        if args.command == "provision" and (not args.node_id or not NODE_ID_PATTERN.fullmatch(args.node_id)):
            raise ProtocolError("valid --node-id is required")
        if args.command in {"backup", "restore"} and args.file is None:
            raise ProtocolError("--file is required")
        if args.command == "restore":
            backup = json.loads(args.file.read_text(encoding="utf-8"))
            validate_backup(backup)
            if not args.confirm:
                print(json.dumps({"status": "dry_run", "source_node_id": backup["source_node_id"], "identity_restored": False}))
                return 0
        pre_reset_state = None
        with BootstrapSerialClient(args.port, timeout=args.timeout) as client:
            if args.command == "read":
                response = client.request("read_node_state")
            elif args.command == "provision":
                state = client.request("read_identity")["result"]
                if state["identity_status"] != "unprovisioned":
                    raise ProtocolError("node is not unprovisioned")
                response = client.request("provision_identity", node_id=args.node_id)
                check = client.request("read_identity")["result"]
                if check.get("node_id") != args.node_id:
                    raise ProtocolError("identity read-back mismatch")
            elif args.command == "verify":
                first = client.request("verify_state")
                second = client.request("verify_state")
                if first["result"] != second["result"]:
                    raise ProtocolError("state changed between verification reads")
                response = second
            elif args.command == "backup":
                response = client.request("backup_state")
                state = response["result"]
                backup = {
                    "backup_schema_version": 1,
                    "created_at_utc": datetime.now(UTC).isoformat(),
                    "source_node_id": state.get("node_id"),
                    "identity_included": bool(args.include_identity),
                    "state": state,
                }
                if not args.include_identity:
                    backup["state"]["node_id"] = None
                backup["canonical_content_hash"] = content_hash(backup)
                args.file.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(args.file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    output.write(json.dumps(backup, indent=2) + "\n")
                try:
                    args.file.chmod(0o600)
                except OSError:
                    pass
                validate_backup(json.loads(args.file.read_text(encoding="utf-8")))
                response = {"status": "success", "file": str(args.file), "sha256": backup["canonical_content_hash"]}
            elif args.command == "restore":
                cfg = backup["state"].get("configuration") or backup["state"]
                allowed = {
                    k: cfg[k]
                    for k in (
                        "sample_interval_ms",
                        "processing_interval_ms",
                        "report_interval_ms",
                        "heartbeat_interval_ms",
                        "filter_type",
                        "filter_window",
                        "enabled",
                    )
                    if k in cfg
                }
                if len(allowed) != 7:
                    raise ProtocolError("backup has no restorable configuration")
                response = client.request("restore_configuration", **allowed)
            else:
                if not args.hardware_id or not HARDWARE_ID_PATTERN.fullmatch(args.hardware_id):
                    raise ProtocolError("normalized --hardware-id is required for factory reset")
                pre_reset_state = client.request("read_node_state")["result"]
                if pre_reset_state.get("hardware_id") != args.hardware_id:
                    raise ProtocolError("connected physical sensor hardware identity mismatch")
                prepared = client.request(
                    "prepare_factory_reset", reset_protocol_version=2, scope=args.scope,
                    expected_hardware_id=args.hardware_id,
                )
                if not args.confirm:
                    client.request("cancel_factory_reset")
                    print(json.dumps({
                        "status": "prepared_then_cancelled", "hardware_id": args.hardware_id,
                        "scope": args.scope, "expires_in_ms": prepared["result"].get("expires_in_ms"),
                    }, indent=2))
                    return 0
                challenge = prepared["result"]
                if challenge.get("hardware_id") != args.hardware_id:
                    raise ProtocolError("reset challenge hardware identity mismatch")
                response = client.request(
                    "confirm_factory_reset", reset_protocol_version=2, scope=args.scope,
                    expected_hardware_id=args.hardware_id, operation_id=challenge["operation_id"],
                    challenge=challenge["challenge"],
                )
        if args.command == "factory-reset" and args.confirm:
            deadline = time.monotonic() + args.reboot_timeout
            verified = None
            while time.monotonic() < deadline and verified is None:
                for port in supported_ports():
                    try:
                        candidate = inspect_port(port, args.timeout)
                    except (OSError, ValueError, ProtocolError, RuntimeError):
                        continue
                    if candidate.get("hardware_id") == args.hardware_id:
                        verified = candidate
                        break
                if verified is None:
                    time.sleep(0.5)
            if verified is None:
                raise ProtocolError("same hardware did not re-enumerate before timeout")
            if verified.get("node_id") is not None or verified.get("identity_status") != "unprovisioned":
                raise ProtocolError("post-reset read-back is not unprovisioned")
            if verified.get("firmware_version") != pre_reset_state.get("firmware_version"):
                raise ProtocolError("firmware version changed across factory reset")
            response = {
                "status": "success", "operation_id": response["result"]["operation_id"],
                "hardware_id": args.hardware_id, "pre_reboot": response["result"],
                "post_reset": verified, "verified_unprovisioned": True,
            }
        print(json.dumps(response, indent=2))
        return 0
    except (OSError, ValueError, ProtocolError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

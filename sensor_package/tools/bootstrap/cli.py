from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from .protocol import NODE_ID_PATTERN, ProtocolError, content_hash, validate_backup
from .serial_client import BootstrapSerialClient


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="XIAO MG24 USB bootstrap tool")
    p.add_argument("command", choices=["read", "provision", "backup", "restore", "verify", "factory-reset"])
    p.add_argument("--port", required=True)
    p.add_argument("--node-id")
    p.add_argument("--file", type=Path)
    p.add_argument("--scope", choices=["configuration_only", "application_factory"], default="configuration_only")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--include-identity", action="store_true")
    p.add_argument("--timeout", type=float, default=3.0)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
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
                args.file.write_text(json.dumps(backup, indent=2) + "\n", encoding="utf-8")
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
                prepared = client.request("prepare_factory_reset", scope=args.scope)
                print(json.dumps(prepared, indent=2))
                if not args.confirm:
                    client.request("cancel_factory_reset")
                    return 0
                token = prepared["result"]["confirmation_token"]
                response = client.request("confirm_factory_reset", scope=args.scope, confirmation_token=token)
        print(json.dumps(response, indent=2))
        return 0
    except (OSError, ValueError, ProtocolError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
FIRMWARE = PACKAGE / "firmware/xiao_mg24_sensor_node"
REGISTRY = FIRMWARE / "application_nvm_keys.h"
SCOPE_FILE = PACKAGE / "reset_scope.json"


def main() -> int:
    header = REGISTRY.read_text(encoding="utf-8")
    pairs = re.findall(r'\{"([a-z0-9_]+)",\s*(k\w+)\}', header)
    constants = {name: int(value, 16) for name, value in re.findall(r"constexpr uint32_t (k\w+) = (0x[0-9A-Fa-f]+)u;", header)}
    registered = {public: constants[symbol] for public, symbol in pairs}
    if len(registered) != len(set(registered.values())) or not all(0x0FF00 <= key <= 0x0FF0F for key in registered.values()):
        raise ValueError("application key registry is duplicate or out of range")
    scope = json.loads(SCOPE_FILE.read_text(encoding="utf-8"))
    for name, targets in scope["scopes"].items():
        unknown = set(targets) - registered.keys()
        if unknown:
            raise ValueError(f"{name} contains unregistered keys: {sorted(unknown)}")
    transaction_keys = scope.get("transaction_keys", [])
    if set(transaction_keys) - registered.keys() or set(transaction_keys) & set(scope["scopes"]["application_factory"]):
        raise ValueError("transaction marker must be registered and excluded from the reset deletion loop")
    source = "\n".join(path.read_text(encoding="utf-8") for path in FIRMWARE.glob("*") if path.suffix in {".h", ".cpp", ".ino"})
    forbidden = ["nvm3_eraseAll", "masserase", "flash erase_sector", "device masserase"]
    found = [token for token in forbidden if token in source]
    if found:
        raise ValueError(f"broad erase reference in application firmware: {found}")
    for path in FIRMWARE.glob("*.cpp"):
        text = path.read_text(encoding="utf-8")
        if path.name != "application_nvm_keys.h" and re.search(r"nvm3_(?:readData|writeData|deleteObject)\([^\n]*0x[0-9A-Fa-f]+", text):
            raise ValueError(f"literal NVM3 key in {path.name}")
    print(json.dumps({"status": "PASS", "registered_keys": registered, "scopes": scope["scopes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"reset-scope validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

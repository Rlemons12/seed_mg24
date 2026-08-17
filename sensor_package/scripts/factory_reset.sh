#!/usr/bin/env bash
set -euo pipefail
[[ $# -ge 2 ]] || { echo "usage: $0 PORT 0x0123456789ABCDEF [--confirm]" >&2; exit 2; }
port="$1"; hardware_id="$2"; shift 2
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root_dir"
python3 -m sensor_package.tools.bootstrap.cli factory-reset --port "$port" --hardware-id "$hardware_id" "$@"

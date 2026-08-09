#!/usr/bin/env bash
set -euo pipefail
[[ $# -ge 1 ]] || { echo "Usage: $0 /dev/ttyACM0 [build-id]" >&2; exit 2; }
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; package_dir="$(cd "$script_dir/.." && pwd)"
"$script_dir/compile.sh" "${2:-local}"
arduino-cli upload -p "$1" --fqbn SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs --input-dir "$package_dir/build" "$package_dir/firmware/xiao_mg24_sensor_node"

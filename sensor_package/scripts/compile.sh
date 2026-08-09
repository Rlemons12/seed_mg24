#!/usr/bin/env bash
set -euo pipefail
package_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; root_dir="$(cd "$package_dir/.." && pwd)"
command -v arduino-cli >/dev/null || { echo 'arduino-cli is required' >&2; exit 127; }
sensor_version="$(tr -d '\r\n' < "$package_dir/VERSION")"; protocol_version="$(tr -d '\r\n' < "$root_dir/shared_protocol/VERSION")"; build_id="${1:-local}"
node_id="UNASSIGNED-MG24"; config="$package_dir/config/device_config.local.h"
if [[ -f "$config" ]]; then node_id="$(sed -nE 's/^#define[[:space:]]+DEVICE_ID[[:space:]]+"([A-Z0-9-]+)"/\1/p' "$config")"; fi
[[ -n "$node_id" ]] || { echo 'Invalid DEVICE_ID' >&2; exit 2; }
git_id="$(git -c safe.directory="$root_dir" -C "$root_dir" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)"; [[ -z "$(git -c safe.directory="$root_dir" -C "$root_dir" status --porcelain 2>/dev/null)" ]] || git_id="${git_id}-dirty"
mkdir -p "$package_dir/build"
flags="-I$package_dir/config -DDEVICE_ID=\"$node_id\" -DSENSOR_PACKAGE_VERSION=\"$sensor_version\" -DFIRMWARE_VERSION=\"$sensor_version\" -DPROTOCOL_VERSION=\"$protocol_version\" -DBUILD_IDENTIFIER=\"$build_id\" -DFIRMWARE_GIT_COMMIT=\"$git_id\""
echo "Building sensor $sensor_version, protocol $protocol_version, node $node_id"
arduino-cli compile --fqbn SiliconLabs:silabs:xiao_mg24:protocol_stack=ble_silabs --build-path "$package_dir/build" --build-property "compiler.cpp.extra_flags=$flags" "$package_dir/firmware/xiao_mg24_sensor_node"

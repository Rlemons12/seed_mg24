#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$root"; python -m sensor_package.tools.bootstrap.cli provision --port "${1:?serial port required}" --node-id "${2:?node_id required}"

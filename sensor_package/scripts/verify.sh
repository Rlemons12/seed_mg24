#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; root_dir="$(cd "$script_dir/../.." && pwd)"
python3 "$root_dir/scripts/verify_compatibility.py"
[[ $# -eq 0 ]] || echo "Verify identity and versions with: arduino-cli monitor -p $1"

#!/usr/bin/env bash
set -euo pipefail
command -v arduino-cli >/dev/null || { echo 'Install Arduino CLI: https://arduino.github.io/arduino-cli/' >&2; exit 127; }
package_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readarray -t values < <(python3 - "$package_dir/toolchain.json" <<'PY'
import json, sys
t=json.load(open(sys.argv[1], encoding='utf-8'))
print(t['board_manager']['package_url']); print(t['board_manager']['core']); print(t['board_manager']['core_version'])
for lib in t['libraries']:
    if lib['source'] == 'Arduino Library Manager': print(f"{lib['name']}@{lib['version']}")
PY
)
url="${values[0]}"; core="${values[1]}"; core_version="${values[2]}"
count="$(arduino-cli config dump | grep -Fxc "        - $url" || true)"
[[ "$count" -le 1 ]] || { echo 'Silicon Labs package URL is duplicated in Arduino CLI configuration' >&2; exit 2; }
[[ "$count" -eq 1 ]] || arduino-cli config add board_manager.additional_urls "$url"
arduino-cli core update-index
arduino-cli core install "$core@$core_version"
for library in "${values[@]:3}"; do arduino-cli lib install "$library"; done

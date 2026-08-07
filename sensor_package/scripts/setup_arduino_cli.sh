#!/usr/bin/env bash
set -euo pipefail
command -v arduino-cli >/dev/null || { echo 'Install Arduino CLI: https://arduino.github.io/arduino-cli/' >&2; exit 127; }
arduino-cli core update-index
arduino-cli core install SiliconLabs:silabs
arduino-cli lib install LSM6DS3

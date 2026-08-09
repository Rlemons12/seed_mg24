#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${SEED_MG24_PROJECT_DIR:-/opt/seed-mg24}"
cd "$PROJECT_DIR"
exec "$PROJECT_DIR/.venv/bin/python" -m gateway

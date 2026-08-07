#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

PROJECT_DIR="${SEED_MG24_PROJECT_DIR:-/opt/seed-mg24}"
SERVICE_USER="${SEED_MG24_SERVICE_USER:-seed-mg24}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

apt-get update
apt-get install -y bluetooth bluez python3 python3-venv python3-pip rsync

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --groups bluetooth "$SERVICE_USER"
else
  usermod -a -G bluetooth "$SERVICE_USER"
fi

install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$PROJECT_DIR" "$PROJECT_DIR/data"
rsync -a --exclude '.git' --exclude '.venv' --exclude '.env' --exclude 'data/' "$SOURCE_DIR/" "$PROJECT_DIR/"
python3 -m venv "$PROJECT_DIR/.venv"
"$PROJECT_DIR/.venv/bin/python" -m pip install --upgrade pip
"$PROJECT_DIR/.venv/bin/python" -m pip install -r "$PROJECT_DIR/gateway/requirements.txt"

if [[ ! -f /etc/seed-mg24-gateway.env ]]; then
  install -m 640 -o root -g "$SERVICE_USER" "$PROJECT_DIR/.env.example" /etc/seed-mg24-gateway.env
fi
install -m 644 "$PROJECT_DIR/gateway/systemd/seed-mg24-gateway.service" /etc/systemd/system/seed-mg24-gateway.service
chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR"
systemctl daemon-reload
systemctl enable seed-mg24-gateway.service

echo "Installed. Review /etc/seed-mg24-gateway.env, then run:"
echo "  sudo systemctl start seed-mg24-gateway"

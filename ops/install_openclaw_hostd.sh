#!/usr/bin/env bash
# install_openclaw_hostd.sh — Idempotent systemd install for openclaw-hostd.
# Run from repo root. Creates /etc/systemd/system/openclaw-hostd.service.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${PYTHON:-/usr/bin/python3}"
UNIT_PATH="/etc/systemd/system/openclaw-hostd.service"

if [ ! -f "$ROOT_DIR/ops/openclaw_hostd.py" ]; then
  echo "ERROR: openclaw_hostd.py not found under $ROOT_DIR" >&2
  exit 1
fi

sudo tee "$UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=OpenClaw host executor (no SSH)
After=network.target

[Service]
Type=simple
ExecStart=$PYTHON $ROOT_DIR/ops/openclaw_hostd.py
WorkingDirectory=$ROOT_DIR
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable openclaw-hostd.service
sudo systemctl restart openclaw-hostd.service
echo "openclaw-hostd: installed and restarted"

# Quick health check
sleep 1
if curl -sSf --connect-timeout 2 "http://127.0.0.1:8877/health" >/dev/null; then
  echo "openclaw-hostd: health OK"
else
  echo "WARNING: hostd health check failed (curl 127.0.0.1:8877/health)" >&2
  exit 1
fi

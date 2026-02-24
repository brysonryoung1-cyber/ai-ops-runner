#!/usr/bin/env bash
# install_openclaw_novnc.sh — Idempotent systemd install for openclaw-novnc.
# Run from repo root. Creates /etc/systemd/system/openclaw-novnc.service.
# Unit is NOT enabled by default; kajabi_capture_interactive starts it on demand.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
UNIT_PATH="/etc/systemd/system/openclaw-novnc.service"

if [ ! -f "$ROOT_DIR/ops/novnc_supervisor.sh" ]; then
  echo "ERROR: novnc_supervisor.sh not found" >&2
  exit 1
fi

chmod +x "$ROOT_DIR/ops/novnc_supervisor.sh"

# Substitute ROOT_DIR in unit (ExecStart path)
sed "s|/opt/ai-ops-runner|$ROOT_DIR|g" "$ROOT_DIR/ops/systemd/openclaw-novnc.service" | sudo tee "$UNIT_PATH" >/dev/null

sudo systemctl daemon-reload
mkdir -p /run/openclaw-novnc

# Persistent Chromium profile for Kajabi (Cloudflare/cookies persist across runs)
KAJABI_PROFILE="/var/lib/openclaw/kajabi_chrome_profile"
sudo mkdir -p "$KAJABI_PROFILE"
sudo chmod 700 "$KAJABI_PROFILE"

echo "openclaw-novnc: unit installed (start on demand via systemctl start openclaw-novnc)"

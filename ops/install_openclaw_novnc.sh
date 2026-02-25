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
[ -f "$ROOT_DIR/ops/novnc_probe.sh" ] && chmod +x "$ROOT_DIR/ops/novnc_probe.sh" || true
[ -f "$ROOT_DIR/ops/guards/novnc_framebuffer_guard.sh" ] && chmod +x "$ROOT_DIR/ops/guards/novnc_framebuffer_guard.sh" || true
[ -f "$ROOT_DIR/ops/scripts/novnc_collect_diagnostics.sh" ] && chmod +x "$ROOT_DIR/ops/scripts/novnc_collect_diagnostics.sh" || true
[ -f "$ROOT_DIR/ops/openclaw_novnc_doctor.sh" ] && chmod +x "$ROOT_DIR/ops/openclaw_novnc_doctor.sh" || true
[ -f "$ROOT_DIR/ops/scripts/novnc_ws_stability_check.py" ] && chmod +x "$ROOT_DIR/ops/scripts/novnc_ws_stability_check.py" || true

# Substitute ROOT_DIR in unit (ExecStart path)
sed "s|/opt/ai-ops-runner|$ROOT_DIR|g" "$ROOT_DIR/ops/systemd/openclaw-novnc.service" | sudo tee "$UNIT_PATH" >/dev/null

sudo systemctl daemon-reload
sudo mkdir -p /run/openclaw-novnc
sudo mkdir -p /run/openclaw

# Persistent Chromium profile for Kajabi (Cloudflare/cookies persist across runs)
KAJABI_PROFILE="/var/lib/openclaw/kajabi_chrome_profile"
sudo mkdir -p "$KAJABI_PROFILE"
sudo chmod 700 "$KAJABI_PROFILE"

# Canonical noVNC display config (single source of truth)
sudo mkdir -p /etc/ai-ops-runner/config
if [ -f "$ROOT_DIR/config/novnc_display.env" ]; then
  sudo cp "$ROOT_DIR/config/novnc_display.env" /etc/ai-ops-runner/config/novnc_display.env
  echo "  novnc_display.env: installed"
fi

echo "openclaw-novnc: unit installed (start on demand via systemctl start openclaw-novnc)"

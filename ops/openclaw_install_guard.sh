#!/usr/bin/env bash
# openclaw_install_guard.sh — Install the OpenClaw guard systemd units.
#
# Copies openclaw-guard.service and openclaw-guard.timer into
# /etc/systemd/system/, reloads systemd, and enables the timer.
#
# Usage: sudo ./ops/openclaw_install_guard.sh
#
# Test mode: set OPENCLAW_GUARD_INSTALL_ROOT to a temp dir prefix
#            (uses stub systemctl in PATH)
#
# Idempotent: safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INSTALL_ROOT="${OPENCLAW_GUARD_INSTALL_ROOT:-}"
SYSTEMD_DIR="${INSTALL_ROOT}/etc/systemd/system"
UNIT_SRC="$ROOT_DIR/ops/systemd"

echo "=== openclaw_install_guard.sh ==="
echo "  Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  Host: $(hostname)"
echo ""

# --- Root check (skipped in test mode) ---
if [ -z "$INSTALL_ROOT" ] && [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: This script must run as root (use sudo)." >&2
  exit 1
fi

# --- Verify source units exist ---
for unit in openclaw-guard.service openclaw-guard.timer; do
  if [ ! -f "$UNIT_SRC/$unit" ]; then
    echo "ERROR: Missing source unit file: $UNIT_SRC/$unit" >&2
    exit 1
  fi
done

# --- Create target directory (for test mode) ---
mkdir -p "$SYSTEMD_DIR"

# --- Copy unit files ---
echo "--- Copying unit files ---"
for unit in openclaw-guard.service openclaw-guard.timer; do
  cp "$UNIT_SRC/$unit" "$SYSTEMD_DIR/$unit"
  chmod 644 "$SYSTEMD_DIR/$unit"
  echo "  Installed: $SYSTEMD_DIR/$unit"
done

# --- Reload systemd + enable timer ---
echo ""
echo "--- Enabling timer ---"
systemctl daemon-reload
systemctl enable --now openclaw-guard.timer
echo "  openclaw-guard.timer: enabled + started"

# --- Verify ---
echo ""
echo "--- Verification ---"
if systemctl is-active --quiet openclaw-guard.timer 2>/dev/null; then
  echo "  openclaw-guard.timer: ACTIVE"
else
  # In test mode with stub systemctl, is-active may not work
  echo "  openclaw-guard.timer: enabled (is-active check skipped or unavailable)"
fi

echo ""
echo "  Guard will run every 10 minutes."
echo "  Check logs: journalctl -u openclaw-guard.service"
echo "  Guard log:  /var/log/openclaw_guard.log"
echo ""
echo "=== openclaw_install_guard.sh COMPLETE ==="
exit 0

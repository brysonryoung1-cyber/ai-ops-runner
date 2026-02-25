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
ALL_UNITS="openclaw-guard.service openclaw-guard.timer openclaw-serve-guard.service openclaw-serve-guard.timer openclaw-novnc-guard.service openclaw-novnc-guard.timer"
for unit in $ALL_UNITS; do
  if [ ! -f "$UNIT_SRC/$unit" ]; then
    echo "ERROR: Missing source unit file: $UNIT_SRC/$unit" >&2
    exit 1
  fi
done

# --- Create target directory (for test mode) ---
mkdir -p "$SYSTEMD_DIR"

# --- Copy unit files ---
echo "--- Copying unit files ---"
for unit in $ALL_UNITS; do
  cp "$UNIT_SRC/$unit" "$SYSTEMD_DIR/$unit"
  chmod 644 "$SYSTEMD_DIR/$unit"
  echo "  Installed: $SYSTEMD_DIR/$unit"
done

# --- Reload systemd + enable timers ---
echo ""
echo "--- Enabling timers ---"
systemctl daemon-reload
systemctl enable --now openclaw-guard.timer
systemctl enable --now openclaw-serve-guard.timer
systemctl enable --now openclaw-novnc-guard.timer
echo "  openclaw-guard.timer: enabled + started"
echo "  openclaw-serve-guard.timer: enabled + started"
echo "  openclaw-novnc-guard.timer: enabled + started"

# --- Verify ---
echo ""
echo "--- Verification ---"
for t in openclaw-guard openclaw-serve-guard openclaw-novnc-guard; do
  if systemctl is-active --quiet "${t}.timer" 2>/dev/null; then
    echo "  ${t}.timer: ACTIVE"
  else
    echo "  ${t}.timer: enabled (is-active check skipped or unavailable)"
  fi
done

echo ""
echo "  Guard will run every 10 minutes."
echo "  Serve guard + noVNC guard: every 2 minutes."
echo "  Check logs: journalctl -u openclaw-guard.service"
echo "  Guard log:  /var/log/openclaw_guard.log"
echo ""
echo "=== openclaw_install_guard.sh COMPLETE ==="
exit 0

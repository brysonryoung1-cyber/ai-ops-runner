#!/usr/bin/env bash
# openclaw_fix_cups_localhost.sh — Idempotent: mask, stop, kill CUPS to eliminate public :631 binds.
set -euo pipefail

echo "=== openclaw_fix_cups_localhost.sh ==="

CUPS_FOUND=0

# Phase 1: Mask first to prevent socket/dbus re-activation, then stop
for unit in cups.service cups.socket cups-browsed.service cups.path; do
  if systemctl list-unit-files "$unit" >/dev/null 2>&1 && \
     systemctl list-unit-files "$unit" 2>/dev/null | grep -q "$unit"; then
    CUPS_FOUND=1
    echo "  Masking + stopping $unit"
    systemctl mask "$unit" 2>/dev/null || true
    systemctl stop "$unit" 2>/dev/null || true
    systemctl disable "$unit" 2>/dev/null || true
  fi
done

if [ "$CUPS_FOUND" -eq 0 ]; then
  echo "  cups not present — nothing to do."
  exit 0
fi

# Phase 2: Kill any lingering cupsd process (mask prevents respawn)
for _kill_attempt in 1 2 3; do
  if ! pgrep -x cupsd >/dev/null 2>&1; then
    break
  fi
  echo "  Killing cupsd (attempt $_kill_attempt)..."
  if [ "$_kill_attempt" -le 2 ]; then
    pkill -x cupsd 2>/dev/null || true
  else
    pkill -9 -x cupsd 2>/dev/null || true
  fi
  sleep 2
done

echo "  Verifying no cupsd binds on :631..."
CUPS_BINDS="$(ss -tlnp 2>/dev/null | grep -E ':(631)\b' | grep -i cupsd || true)"
if [ -n "$CUPS_BINDS" ]; then
  echo "  FAIL: cupsd still bound on :631 after remediation:" >&2
  echo "$CUPS_BINDS" >&2
  exit 1
fi

echo "  PASS: no cupsd binds on :631."
exit 0

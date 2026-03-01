#!/usr/bin/env bash
# openclaw_fix_cups_localhost.sh — Idempotent: mask, stop, kill CUPS to eliminate public :631 binds.
set -euo pipefail

echo "=== openclaw_fix_cups_localhost.sh ==="

CUPS_FOUND=0

# Phase 1: Mask units to block all restart paths, then stop
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

# Phase 1b: Disable dbus activation (cups registers org.cups.cupsd via dbus)
for dbus_svc in /usr/share/dbus-1/system-services/org.cups.cupsd.service \
                /usr/share/dbus-1/system-services/org.cups.*.service; do
  if [ -f "$dbus_svc" ] 2>/dev/null; then
    CUPS_FOUND=1
    echo "  Disabling dbus activation: $dbus_svc"
    mv "$dbus_svc" "${dbus_svc}.disabled" 2>/dev/null || true
  fi
done

systemctl daemon-reload 2>/dev/null || true

if [ "$CUPS_FOUND" -eq 0 ]; then
  echo "  cups not present — nothing to do."
  exit 0
fi

# Phase 2: Kill cupsd and anything on port 631
for _kill_attempt in 1 2 3; do
  if ! pgrep -x cupsd >/dev/null 2>&1; then
    break
  fi
  echo "  Killing cupsd (attempt $_kill_attempt, SIGKILL)..."
  kill -9 $(pgrep -x cupsd) 2>/dev/null || true
  sleep 2
done

# Phase 2b: Use fuser to kill anything still bound to 631
if command -v fuser >/dev/null 2>&1; then
  if fuser 631/tcp >/dev/null 2>&1; then
    echo "  Killing processes on port 631 via fuser..."
    fuser -k 631/tcp 2>/dev/null || true
    sleep 2
    fuser -k -9 631/tcp 2>/dev/null || true
    sleep 2
  fi
fi

echo "  Verifying no cupsd binds on :631..."
CUPS_BINDS="$(ss -tlnp 2>/dev/null | grep -E ':(631)\b' || true)"
if [ -n "$CUPS_BINDS" ]; then
  echo "  FAIL: port 631 still bound after remediation:" >&2
  echo "$CUPS_BINDS" >&2
  exit 1
fi

echo "  PASS: no binds on :631."
exit 0

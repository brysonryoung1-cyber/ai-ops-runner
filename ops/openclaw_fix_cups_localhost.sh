#!/usr/bin/env bash
# openclaw_fix_cups_localhost.sh — Idempotent: stop, disable, mask CUPS to eliminate public :631 binds.
set -euo pipefail

echo "=== openclaw_fix_cups_localhost.sh ==="

CUPS_FOUND=0

for unit in cups.service cups.socket cups-browsed.service; do
  if systemctl list-unit-files "$unit" >/dev/null 2>&1 && \
     systemctl list-unit-files "$unit" 2>/dev/null | grep -q "$unit"; then
    CUPS_FOUND=1
    echo "  Stopping + disabling + masking $unit"
    systemctl stop "$unit" 2>/dev/null || true
    systemctl disable "$unit" 2>/dev/null || true
    systemctl mask "$unit" 2>/dev/null || true
  fi
done

if [ "$CUPS_FOUND" -eq 0 ]; then
  echo "  cups not present — nothing to do."
  exit 0
fi

echo "  Verifying no cupsd binds on :631..."
CUPS_BINDS="$(ss -tlnp 2>/dev/null | grep -E ':(631)\b' | grep -i cupsd || true)"
if [ -n "$CUPS_BINDS" ]; then
  echo "  FAIL: cupsd still bound on :631 after remediation:" >&2
  echo "$CUPS_BINDS" >&2
  exit 1
fi

echo "  PASS: no cupsd binds on :631."
exit 0

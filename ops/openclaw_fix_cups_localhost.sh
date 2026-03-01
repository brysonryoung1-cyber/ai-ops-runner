#!/usr/bin/env bash
# openclaw_fix_cups_localhost.sh — Idempotent: reconfigure CUPS to bind localhost only (no public :631).
set -euo pipefail

echo "=== openclaw_fix_cups_localhost.sh ==="

CUPSD_CONF="/etc/cups/cupsd.conf"

# If cups is not installed at all, exit cleanly
if ! command -v cupsd >/dev/null 2>&1 && [ ! -f "$CUPSD_CONF" ]; then
  echo "  cups not present — nothing to do."
  exit 0
fi

if [ ! -f "$CUPSD_CONF" ]; then
  echo "  cups not present (no cupsd.conf) — nothing to do."
  exit 0
fi

echo "  Found $CUPSD_CONF"

# Phase 1: Reconfigure cupsd to bind localhost only
# Replace "Listen" and "Port" directives that bind publicly
CHANGED=0

# Check if already configured for localhost-only
if grep -qE '^\s*Listen\s+(localhost|127\.0\.0\.1):631' "$CUPSD_CONF" && \
   ! grep -qE '^\s*(Listen\s+(\*|0\.0\.0\.0|::)|Port\s+631)' "$CUPSD_CONF"; then
  echo "  cupsd.conf already binds localhost:631 only — no changes needed."
else
  echo "  Reconfiguring cupsd.conf to bind localhost:631 only..."
  cp "$CUPSD_CONF" "${CUPSD_CONF}.bak.$(date +%s)"

  # Comment out any public Listen/Port directives
  sed -i 's/^\(\s*Listen\s\+\*:631\)/#\1 # disabled by openclaw/' "$CUPSD_CONF"
  sed -i 's/^\(\s*Listen\s\+0\.0\.0\.0:631\)/#\1 # disabled by openclaw/' "$CUPSD_CONF"
  sed -i 's/^\(\s*Listen\s\+\[::\]:631\)/#\1 # disabled by openclaw/' "$CUPSD_CONF"
  sed -i 's/^\(\s*Port\s\+631\)/#\1 # disabled by openclaw/' "$CUPSD_CONF"

  # Ensure Listen localhost:631 is present
  if ! grep -qE '^\s*Listen\s+(localhost|127\.0\.0\.1):631' "$CUPSD_CONF"; then
    # Add after the first commented-out Listen line, or at top
    if grep -q '# disabled by openclaw' "$CUPSD_CONF"; then
      sed -i '/# disabled by openclaw/{a\Listen localhost:631
      ;:a;n;ba}' "$CUPSD_CONF"
    else
      sed -i '1i\Listen localhost:631' "$CUPSD_CONF"
    fi
  fi

  CHANGED=1
  echo "  cupsd.conf updated: public listeners disabled, localhost:631 added."
fi

# Phase 2: Restart cupsd to apply config
if [ "$CHANGED" -eq 1 ]; then
  echo "  Restarting cups..."
  systemctl restart cups.service 2>/dev/null || systemctl restart cups 2>/dev/null || true
  sleep 3
fi

# Phase 3: Verify no public cupsd binds on :631
echo "  Verifying no public cupsd binds on :631..."
PUBLIC_CUPS="$(ss -tlnp 2>/dev/null | grep -E ':(631)\b' | grep -vE '127\.0\.0\.1|::1|\[::1\]' || true)"
if [ -n "$PUBLIC_CUPS" ]; then
  echo "  FAIL: cupsd still has public binds on :631:" >&2
  echo "$PUBLIC_CUPS" >&2
  exit 1
fi

echo "  PASS: no public cupsd binds on :631."
exit 0

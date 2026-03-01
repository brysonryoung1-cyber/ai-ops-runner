#!/usr/bin/env bash
# openclaw_fix_cups_localhost.sh — Idempotent: reconfigure or stop CUPS to eliminate public :631 binds.
set -euo pipefail

echo "=== openclaw_fix_cups_localhost.sh ==="

# Detect by port binding — the ground truth that matters
PUBLIC_631="$(ss -tlnp 2>/dev/null | grep -E ':(631)\b' | grep -vE '127\.0\.0\.1|::1|\[::1\]' || true)"
if [ -z "$PUBLIC_631" ]; then
  echo "  No public binds on :631 — nothing to do."
  exit 0
fi

echo "  Public :631 binds detected:"
echo "$PUBLIC_631"

# Find cupsd.conf (may be at different paths)
CUPSD_CONF=""
for conf in /etc/cups/cupsd.conf /usr/local/etc/cups/cupsd.conf; do
  if [ -f "$conf" ]; then
    CUPSD_CONF="$conf"
    break
  fi
done

CHANGED=0
if [ -n "$CUPSD_CONF" ]; then
  echo "  Found $CUPSD_CONF — reconfiguring to localhost-only..."
  cp "$CUPSD_CONF" "${CUPSD_CONF}.bak.$(date +%s)"

  # Comment out public Listen/Port directives
  sed -i 's/^\(\s*Listen\s\+\*:631\)/#\1 # disabled by openclaw/' "$CUPSD_CONF" 2>/dev/null || true
  sed -i 's/^\(\s*Listen\s\+0\.0\.0\.0:631\)/#\1 # disabled by openclaw/' "$CUPSD_CONF" 2>/dev/null || true
  sed -i 's/^\(\s*Listen\s\+\[::\]:631\)/#\1 # disabled by openclaw/' "$CUPSD_CONF" 2>/dev/null || true
  sed -i 's/^\(\s*Port\s\+631\)/#\1 # disabled by openclaw/' "$CUPSD_CONF" 2>/dev/null || true

  # Ensure Listen localhost:631 is present
  if ! grep -qE '^\s*Listen\s+(localhost|127\.0\.0\.1):631' "$CUPSD_CONF"; then
    echo "Listen localhost:631" >> "$CUPSD_CONF"
  fi

  CHANGED=1
  echo "  cupsd.conf updated."
fi

# Mask systemd units to prevent future public activation
for unit in cups.service cups.socket cups-browsed.service cups.path; do
  systemctl mask "$unit" 2>/dev/null || true
  systemctl stop "$unit" 2>/dev/null || true
done
systemctl daemon-reload 2>/dev/null || true

if [ "$CHANGED" -eq 1 ]; then
  # Restart cupsd so config takes effect (it's masked but restart still works for active units)
  systemctl restart cups 2>/dev/null || systemctl restart cups.service 2>/dev/null || true
  sleep 3
fi

# Kill cupsd processes and use fuser as fallback
for _kill_attempt in 1 2 3; do
  PUBLIC_631="$(ss -tlnp 2>/dev/null | grep -E ':(631)\b' | grep -vE '127\.0\.0\.1|::1|\[::1\]' || true)"
  [ -z "$PUBLIC_631" ] && break
  echo "  Still bound publicly (attempt $_kill_attempt) — killing..."
  pgrep -f cupsd | xargs -r kill -9 2>/dev/null || true
  command -v fuser >/dev/null 2>&1 && fuser -k -9 631/tcp 2>/dev/null || true
  sleep 3
done

# Final verification
echo "  Verifying no public binds on :631..."
PUBLIC_631="$(ss -tlnp 2>/dev/null | grep -E ':(631)\b' | grep -vE '127\.0\.0\.1|::1|\[::1\]' || true)"
if [ -n "$PUBLIC_631" ]; then
  echo "  FAIL: port 631 still has public binds after remediation:" >&2
  echo "$PUBLIC_631" >&2
  exit 1
fi

echo "  PASS: no public binds on :631."
exit 0

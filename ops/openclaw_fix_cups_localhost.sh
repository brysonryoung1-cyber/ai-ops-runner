#!/usr/bin/env bash
# openclaw_fix_cups_localhost.sh — Idempotent: stop CUPS (system or snap) to eliminate public :631 binds.
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

# --- Strategy 1: Snap-based CUPS (Ubuntu 22.04+) ---
if command -v snap >/dev/null 2>&1 && snap list cups >/dev/null 2>&1; then
  echo "  CUPS is a snap package — stopping and disabling..."
  snap stop cups 2>/dev/null || true
  snap disable cups 2>/dev/null || true

  for unit in snap.cups.cupsd.service snap.cups.cups-browsed.service; do
    systemctl stop "$unit" 2>/dev/null || true
    systemctl mask "$unit" 2>/dev/null || true
  done
  systemctl daemon-reload 2>/dev/null || true
  sleep 3
fi

# --- Strategy 2: System-packaged CUPS ---
for unit in cups.service cups.socket cups-browsed.service cups.path; do
  systemctl mask "$unit" 2>/dev/null || true
  systemctl stop "$unit" 2>/dev/null || true
done
systemctl daemon-reload 2>/dev/null || true

# --- Strategy 3: Reconfigure cupsd.conf if present ---
for conf in /etc/cups/cupsd.conf /usr/local/etc/cups/cupsd.conf /snap/cups/current/etc/cups/cupsd.conf; do
  if [ -f "$conf" ] && [ -w "$conf" ]; then
    echo "  Reconfiguring $conf to localhost-only..."
    cp "$conf" "${conf}.bak.$(date +%s)" 2>/dev/null || true
    sed -i 's/^\(\s*Listen\s\+\*:631\)/#\1 # disabled by openclaw/' "$conf" 2>/dev/null || true
    sed -i 's/^\(\s*Listen\s\+0\.0\.0\.0:631\)/#\1 # disabled by openclaw/' "$conf" 2>/dev/null || true
    sed -i 's/^\(\s*Listen\s\+\[::\]:631\)/#\1 # disabled by openclaw/' "$conf" 2>/dev/null || true
    sed -i 's/^\(\s*Port\s\+631\)/#\1 # disabled by openclaw/' "$conf" 2>/dev/null || true
    if ! grep -qE '^\s*Listen\s+(localhost|127\.0\.0\.1):631' "$conf"; then
      echo "Listen localhost:631" >> "$conf"
    fi
  fi
done

# --- Kill any remaining cupsd processes ---
for _kill_attempt in 1 2 3; do
  PUBLIC_631="$(ss -tlnp 2>/dev/null | grep -E ':(631)\b' | grep -vE '127\.0\.0\.1|::1|\[::1\]' || true)"
  [ -z "$PUBLIC_631" ] && break
  echo "  Still bound publicly (attempt $_kill_attempt) — killing..."
  pgrep -f cupsd | xargs -r kill -9 2>/dev/null || true
  command -v fuser >/dev/null 2>&1 && fuser -k -9 631/tcp 2>/dev/null || true
  sleep 3
done

# --- Final verification ---
echo "  Verifying no public binds on :631..."
PUBLIC_631="$(ss -tlnp 2>/dev/null | grep -E ':(631)\b' | grep -vE '127\.0\.0\.1|::1|\[::1\]' || true)"
if [ -n "$PUBLIC_631" ]; then
  echo "  FAIL: port 631 still has public binds after remediation:" >&2
  echo "$PUBLIC_631" >&2
  exit 1
fi

echo "  PASS: no public binds on :631."
exit 0

#!/usr/bin/env bash
# novnc_phase0_evidence.sh — Phase 0: Capture deployed reality on aiops-1.
#
# Writes to artifacts/novnc_debug/client_fail_repro/<run_id>/
#   serve_status.txt, serve_status.json
#   frontdoor_status.txt (if openclaw-frontdoor exists)
#   ports.txt, systemd_status.txt, journal_tail.txt
#   ws_probe.json
#
# Run on aiops-1 (or via SSH). No secrets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d%H%M%S)_phase0}"
ART_DIR="$ROOT_DIR/artifacts/novnc_debug/client_fail_repro/$RUN_ID"
NOVNC_PORT="${OPENCLAW_NOVNC_PORT:-6080}"
CONSOLE_PORT="${OPENCLAW_CONSOLE_PORT:-8787}"
FRONTDOOR_PORT="${OPENCLAW_FRONTDOOR_PORT:-8788}"

mkdir -p "$ART_DIR"

TS_HOSTNAME="aiops-1.tailc75c62.ts.net"
if command -v tailscale >/dev/null 2>&1; then
  TS_HOSTNAME="$(tailscale status --json 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print((d.get('Self') or {}).get('DNSName', '').rstrip('.') or 'aiops-1.tailc75c62.ts.net')
except: print('aiops-1.tailc75c62.ts.net')
" 2>/dev/null)"
fi

echo "=== Phase 0: noVNC client fail repro evidence ==="
echo "  Run ID: $RUN_ID"
echo "  Artifacts: $ART_DIR"
echo ""

# 1. Tailscale serve (full output)
tailscale serve status 2>/dev/null >"$ART_DIR/serve_status.txt" || echo "tailscale serve unavailable" >"$ART_DIR/serve_status.txt"
tailscale serve status --json 2>/dev/null >"$ART_DIR/serve_status.json" || echo '{}' >"$ART_DIR/serve_status.json"

# 2. Frontdoor (if exists)
if systemctl list-unit-files 2>/dev/null | grep -q openclaw-frontdoor; then
  systemctl status openclaw-frontdoor.service --no-pager 2>/dev/null >"$ART_DIR/frontdoor_status.txt" || echo "frontdoor status unavailable" >"$ART_DIR/frontdoor_status.txt"
  curl -fsS --connect-timeout 2 "http://127.0.0.1:$FRONTDOOR_PORT/api/ui/health_public" 2>/dev/null >"$ART_DIR/frontdoor_health.json" || echo '{"ok":false}' >"$ART_DIR/frontdoor_health.json"
else
  echo "openclaw-frontdoor not installed" >"$ART_DIR/frontdoor_status.txt"
fi

# 3. Listeners and owners (8787, 6080, 5900)
ss -lntp 2>/dev/null | grep -E ":$NOVNC_PORT|:6080|:8787|:5900|:8788" >"$ART_DIR/ports.txt" 2>/dev/null || echo "no matching ports" >"$ART_DIR/ports.txt"
lsof -i ":$NOVNC_PORT" -i ":8787" -i ":5900" -i ":8788" 2>/dev/null >>"$ART_DIR/ports.txt" || true

# 4. openclaw-novnc systemd + journal (last 300 lines)
systemctl status openclaw-novnc.service --no-pager 2>/dev/null >"$ART_DIR/systemd_status.txt" || echo "service not found" >"$ART_DIR/systemd_status.txt"
journalctl -u openclaw-novnc.service -n 300 --no-pager 2>/dev/null >"$ART_DIR/journal_tail.txt" || echo "journal unavailable" >"$ART_DIR/journal_tail.txt"

# 5. WSS probe (authoritative)
mkdir -p "$ROOT_DIR/artifacts/novnc_debug/ws_probe/$RUN_ID"
OPENCLAW_TS_HOSTNAME="$TS_HOSTNAME" OPENCLAW_WS_PROBE_HOLD_SEC=10 \
  python3 "$SCRIPT_DIR/novnc_ws_probe.py" --host "$TS_HOSTNAME" --all 2>/dev/null >"$ROOT_DIR/artifacts/novnc_debug/ws_probe/$RUN_ID/ws_probe.json" || echo '{"all_ok":false}' >"$ROOT_DIR/artifacts/novnc_debug/ws_probe/$RUN_ID/ws_probe.json"
cp "$ROOT_DIR/artifacts/novnc_debug/ws_probe/$RUN_ID/ws_probe.json" "$ART_DIR/ws_probe.json" 2>/dev/null || true

echo "Evidence written to $ART_DIR"
echo "  serve_status.txt, serve_status.json"
echo "  frontdoor_status.txt, ports.txt, systemd_status.txt, journal_tail.txt"
echo "  ws_probe.json"
exit 0

#!/usr/bin/env bash
# novnc_ws_upgrade_selftest.sh — Assert WebSocket upgrade returns 101 at all hops.
#
# Runs hop-by-hop probe and fails if any hop returns non-101.
# Used by: novnc_stack_doctor, system.canary, ship_deploy_verify Phase 2b diagnostics.
#
# Exit 0 only when all hops return 101 Switching Protocols.
# SKIP: when not on production (openclaw-novnc not installed and 6080 not listening).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-selftest_$(date -u +%Y%m%dT%H%M%SZ)}"
TS_HOSTNAME="${OPENCLAW_TS_HOSTNAME:-aiops-1.tailc75c62.ts.net}"
ARTIFACTS="${OPENCLAW_ARTIFACTS_ROOT:-$ROOT_DIR/artifacts}"

# Skip when not on production (matches novnc_probe_selftest pattern)
if ! systemctl is-active --quiet openclaw-novnc 2>/dev/null; then
  if ! ss -tln 2>/dev/null | grep -qE ':6080[^0-9]|6080 '; then
    echo "  SKIP: openclaw-novnc not installed and 6080 not listening (deploy installs on aiops-1)"
    exit 0
  fi
fi

probe_status() {
  local url="$1"
  curl -isS --http1.1 --max-time 8 --connect-timeout 5 \
    -H "Connection: Upgrade" \
    -H "Upgrade: websocket" \
    -H "Sec-WebSocket-Version: 13" \
    -H "Sec-WebSocket-Key: SGVsbG8sIHdvcmxkIQ==" \
    "$url" 2>/dev/null | head -1 | tr -d '\r' | grep -oE 'HTTP/[0-9.]+ [0-9]+' || echo "UNREACHABLE"
}

PASS=true
RESULTS=""

for hop_label_url in \
  "A_6080|http://127.0.0.1:6080/websockify" \
  "B_8788|http://127.0.0.1:8788/websockify" \
  "C_443|https://${TS_HOSTNAME}/websockify" \
  "D_443_alias|https://${TS_HOSTNAME}/novnc/websockify"; do

  label="${hop_label_url%%|*}"
  url="${hop_label_url##*|}"
  status="$(probe_status "$url")"

  if echo "$status" | grep -q "101"; then
    echo "  $label: PASS ($status)"
  else
    echo "  $label: FAIL ($status)"
    PASS=false
  fi
  RESULTS="${RESULTS}\"${label}\":\"${status}\","
done

RESULTS="${RESULTS%,}"

if [ "$PASS" = true ]; then
  echo '{"ok":true,"run_id":"'"$RUN_ID"'",'"$RESULTS"'}'
  exit 0
else
  echo '{"ok":false,"run_id":"'"$RUN_ID"'",'"$RESULTS"'}'
  # On failure, run full hop probe for diagnostics if available
  if [ -f "$ROOT_DIR/ops/scripts/ws_upgrade_hop_probe.sh" ]; then
    OPENCLAW_HOP_PROBE_RUN_ID="${RUN_ID}_diag" \
      bash "$ROOT_DIR/ops/scripts/ws_upgrade_hop_probe.sh" 2>&1 | tail -5 || true
  fi
  exit 1
fi

#!/usr/bin/env bash
# openclaw_novnc_routing_fix.sh — One-click fix for noVNC routing (frontdoor + single-root Serve).
#
# 1. Installs frontdoor if missing (Caddy on 127.0.0.1:8788)
# 2. Restarts: openclaw-novnc, openclaw-frontdoor (correct order)
# 3. Tailscale Serve: single-root to http://127.0.0.1:8788 (NO per-path handlers)
# 4. Runs openclaw_novnc_doctor + novnc_ws_probe (WSS over 443)
# 5. Writes proof to artifacts/hq_proofs/frontdoor_fix/<run_id>/
#
# Fail-closed: exits nonzero if ws_probe or doctor fails.
# Run on aiops-1. No secrets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d%H%M%SZ)_novnc_routing}"
PROOF_DIR="$ROOT_DIR/artifacts/hq_proofs/frontdoor_fix/$RUN_ID"
FRONTDOOR_PORT="${OPENCLAW_FRONTDOOR_PORT:-8788}"
mkdir -p "$PROOF_DIR"

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

echo "=== openclaw_novnc_routing_fix ==="
echo "  Run ID: $RUN_ID"
echo "  Host: $TS_HOSTNAME"
echo ""

# 1. Install frontdoor if missing
if ! systemctl is-active --quiet openclaw-frontdoor.service 2>/dev/null; then
  if [ -f "$ROOT_DIR/ops/install_openclaw_frontdoor.sh" ]; then
    echo "Installing frontdoor..."
    sudo ./ops/install_openclaw_frontdoor.sh 2>&1 | tail -5
  else
    echo "WARNING: install_openclaw_frontdoor.sh not found; frontdoor may not be installed" >&2
  fi
fi

# 2. Restart services (novnc first, then frontdoor)
echo "Restarting openclaw-novnc..."
systemctl restart openclaw-novnc 2>/dev/null || true
sleep 3
echo "Restarting openclaw-frontdoor..."
systemctl restart openclaw-frontdoor 2>/dev/null || true
sleep 2

# 3. Tailscale Serve: single-root to frontdoor
echo "Applying Tailscale Serve: single-root -> 127.0.0.1:$FRONTDOOR_PORT"
tailscale serve reset 2>/dev/null || true
tailscale serve --bg --https=443 "http://127.0.0.1:$FRONTDOOR_PORT" 2>/dev/null || true
sleep 2

# 4. Doctor (fast then full)
echo "Running noVNC doctor..."
OPENCLAW_RUN_ID="${RUN_ID}_fast" "$ROOT_DIR/ops/openclaw_novnc_doctor.sh" --fast 2>/dev/null | tail -1 > "$PROOF_DIR/doctor_fast.json" || echo '{"ok":false}' > "$PROOF_DIR/doctor_fast.json"
OPENCLAW_RUN_ID="$RUN_ID" "$ROOT_DIR/ops/openclaw_novnc_doctor.sh" 2>/dev/null | tail -1 > "$PROOF_DIR/doctor.json" || echo '{"ok":false}' > "$PROOF_DIR/doctor.json"

# 5. WSS probe (WSS over 443 — same as browser)
WS_PROBE="$ROOT_DIR/ops/scripts/novnc_ws_probe.py"
WS_PROBE_OK="false"
if [ -x "$WS_PROBE" ]; then
  if OPENCLAW_TS_HOSTNAME="$TS_HOSTNAME" OPENCLAW_WS_PROBE_HOLD_SEC=10 python3 "$WS_PROBE" --host "$TS_HOSTNAME" --all 2>/dev/null > "$PROOF_DIR/ws_probe.json"; then
    WS_PROBE_OK="true"
  fi
else
  echo '{"all_ok":false,"error":"ws_probe not found"}' > "$PROOF_DIR/ws_probe.json"
fi

# Extract novnc_url
NOVNC_URL=""
if [ -f "$PROOF_DIR/doctor.json" ]; then
  NOVNC_URL="$(python3 -c "import json; d=json.load(open('$PROOF_DIR/doctor.json')); print(d.get('novnc_url','') or '')" 2>/dev/null)" || true
fi
[ -z "$NOVNC_URL" ] && NOVNC_URL="https://${TS_HOSTNAME}/novnc/vnc.html?autoconnect=1&path=/websockify"

# HTTP checks
NOVNC_HTTP_200="false"
[ "$(curl -kfsS --connect-timeout 3 "https://${TS_HOSTNAME}/novnc/vnc.html" -o /dev/null -w "%{http_code}" 2>/dev/null)" = "200" ] && NOVNC_HTTP_200="true"

# Write PROOF.md
cat > "$PROOF_DIR/PROOF.md" << EOF
# Frontdoor Fix Proof

**Run ID:** $RUN_ID
**Timestamp:** $(date -u +%Y-%m-%dT%H:%M:%SZ)

## Architecture
- Tailscale Serve: single-root \`https://* -> http://127.0.0.1:$FRONTDOOR_PORT\`
- Frontdoor (Caddy): routes /api/* -> 8787, /novnc/*, /websockify -> 6080

## Checks
- novnc_http_200: $NOVNC_HTTP_200
- ws_probe (WSS 443): $WS_PROBE_OK
- doctor: $(python3 -c "import json; d=json.load(open('$PROOF_DIR/doctor.json')); print('PASS' if d.get('ok') else 'FAIL')" 2>/dev/null || echo "unknown")

## Canonical noVNC URL
$NOVNC_URL

## Artifacts
- doctor.json, doctor_fast.json, ws_probe.json
EOF

# Fail-closed if ws_probe or doctor failed
DOCTOR_OK="false"
[ -f "$PROOF_DIR/doctor.json" ] && python3 -c "import json; d=json.load(open('$PROOF_DIR/doctor.json')); exit(0 if d.get('ok') else 1)" 2>/dev/null && DOCTOR_OK="true"

if [ "$WS_PROBE_OK" != "true" ]; then
  echo "FAIL: ws_probe did not pass (WSS over 443)" >&2
  cat "$PROOF_DIR/ws_probe.json" 2>/dev/null | head -20
  exit 1
fi
if [ "$DOCTOR_OK" != "true" ]; then
  echo "FAIL: openclaw_novnc_doctor did not pass" >&2
  exit 1
fi

echo ""
echo "Proof: $PROOF_DIR/PROOF.md"
echo "novnc_url_canonical: $NOVNC_URL"
exit 0

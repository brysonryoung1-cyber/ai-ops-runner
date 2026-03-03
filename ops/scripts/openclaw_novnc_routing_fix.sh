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

# Gate-aware suppression: skip disruptive actions during active human login gate
_STATE_ROOT="${OPENCLAW_STATE_ROOT:-/opt/ai-ops-runner/state}"
_GATE_FILE="$_STATE_ROOT/human_gate/soma_kajabi.json"
if [ -f "$_GATE_FILE" ] && [ "${OPENCLAW_FORCE_AUTORECOVER:-0}" != "1" ]; then
  _expires="$(python3 -c "
import json, sys
from datetime import datetime, timezone
try:
    g = json.load(open('$_GATE_FILE'))
    ea = datetime.fromisoformat(g['expires_at'])
    if datetime.now(timezone.utc) < ea:
        print('active')
except: pass
" 2>/dev/null || true)"
  if [ "$_expires" = "active" ]; then
    echo "openclaw_novnc_routing_fix: suppressed — human gate active (set OPENCLAW_FORCE_AUTORECOVER=1 to override)"
    exit 0
  fi
fi

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
if ! systemctl restart openclaw-novnc 2>/dev/null; then
  echo "  restart failed; attempting shm_fix recovery..."
  [ -f "$ROOT_DIR/ops/scripts/novnc_shm_fix.sh" ] && bash "$ROOT_DIR/ops/scripts/novnc_shm_fix.sh" 2>&1 | tail -5
  systemctl restart openclaw-novnc 2>/dev/null || true
fi

# Bounded readiness loop: require port + HTTP + WS probe (<=60s)
NOVNC_READY=0
for _readiness_i in $(seq 1 30); do
  PORT_OK=0; HTTP_OK=0; WS_OK=0
  ss -tln 2>/dev/null | grep -qE ':6080[^0-9]|:6080$' && PORT_OK=1
  [ "$PORT_OK" -eq 1 ] && curl -sf --connect-timeout 3 http://127.0.0.1:6080/vnc.html > /dev/null 2>&1 && HTTP_OK=1
  if [ "$HTTP_OK" -eq 1 ]; then
    WS_PROBE_SCRIPT=""
    [ -f "$ROOT_DIR/ops/scripts/novnc_ws_stability_check.py" ] && WS_PROBE_SCRIPT="$ROOT_DIR/ops/scripts/novnc_ws_stability_check.py"
    [ -z "$WS_PROBE_SCRIPT" ] && [ -f "$ROOT_DIR/ops/scripts/novnc_ws_probe.py" ] && WS_PROBE_SCRIPT="$ROOT_DIR/ops/scripts/novnc_ws_probe.py"
    if [ -n "$WS_PROBE_SCRIPT" ]; then
      OPENCLAW_WS_PROBE_HOLD_SEC=10 python3 "$WS_PROBE_SCRIPT" --host 127.0.0.1 --all >/dev/null 2>&1 && WS_OK=1
    else
      WS_OK=1
    fi
  fi
  if [ "$PORT_OK" -eq 1 ] && [ "$HTTP_OK" -eq 1 ] && [ "$WS_OK" -eq 1 ]; then
    NOVNC_READY=1
    echo "  noVNC ready (port=$PORT_OK http=$HTTP_OK ws=$WS_OK) after ${_readiness_i} iterations"
    break
  fi
  sleep 2
done

if [ "$NOVNC_READY" -eq 0 ]; then
  echo "  FAIL: noVNC readiness not achieved after 30 iterations (port=$PORT_OK http=$HTTP_OK ws=$WS_OK)" >&2
  mkdir -p "$PROOF_DIR"
  systemctl status openclaw-novnc.service --no-pager > "$PROOF_DIR/readiness_fail_status.txt" 2>&1 || true
  journalctl -u openclaw-novnc.service -n 100 --no-pager > "$PROOF_DIR/readiness_fail_journal.txt" 2>&1 || true
fi

echo "Restarting openclaw-frontdoor..."
systemctl restart openclaw-frontdoor 2>/dev/null || true
sleep 2

# 3. Tailscale Serve: TCP mode (443 → Caddy TLS 8443, WebSocket-safe)
echo "Applying Tailscale Serve: TCP 443 -> 127.0.0.1:8443"
tailscale serve reset 2>/dev/null || true
tailscale serve --bg --tcp=443 "tcp://127.0.0.1:8443" 2>/dev/null || true
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
[ -z "$NOVNC_URL" ] && NOVNC_URL="https://${TS_HOSTNAME}/novnc/vnc.html?autoconnect=1&reconnect=true&reconnect_delay=2000&path=/websockify"

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

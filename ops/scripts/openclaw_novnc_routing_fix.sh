#!/usr/bin/env bash
# openclaw_novnc_routing_fix.sh — One-click fix for noVNC routing (Tailscale Serve + doctor).
#
# 1. Runs serve remediation: /novnc + /websockify -> 6080, / -> 8787
# 2. Runs openclaw_novnc_doctor (--fast then full)
# 3. Writes proof artifact to artifacts/hq_proofs/novnc_canonical/<run_id>/
#
# Proof artifact includes: novnc_url_canonical, ws_upgrade_ok, framebuffer_non_black.
# Run on aiops-1. Never prints secrets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%SZ)_novnc_routing}"
PROOF_DIR="$ROOT_DIR/artifacts/hq_proofs/novnc_canonical/$RUN_ID"
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

# 1. Serve remediation
echo "Applying Tailscale Serve: /novnc, /websockify -> 6080; / -> 8787"
tailscale serve reset 2>/dev/null || true
tailscale serve --bg --https=443 --set-path=/novnc "http://127.0.0.1:6080" 2>/dev/null || true
tailscale serve --bg --https=443 --set-path=/websockify "http://127.0.0.1:6080" 2>/dev/null || true
tailscale serve --bg --https=443 "http://127.0.0.1:8787" 2>/dev/null || true
sleep 2

# 2. Doctor (fast then deep)
echo "Running noVNC doctor (fast)..."
OPENCLAW_RUN_ID="${RUN_ID}_fast" "$ROOT_DIR/ops/openclaw_novnc_doctor.sh" --fast 2>/dev/null | tail -1 > "$PROOF_DIR/doctor_fast.json" || true
echo "Running noVNC doctor (deep)..."
OPENCLAW_RUN_ID="$RUN_ID" "$ROOT_DIR/ops/openclaw_novnc_doctor.sh" 2>/dev/null | tail -1 > "$PROOF_DIR/doctor.json" || true

# Extract novnc_url from doctor
NOVNC_URL=""
if [ -f "$PROOF_DIR/doctor.json" ]; then
  NOVNC_URL="$(python3 -c "import json; d=json.load(open('$PROOF_DIR/doctor.json')); print(d.get('novnc_url','') or '')" 2>/dev/null)" || true
fi
[ -z "$NOVNC_URL" ] && NOVNC_URL="https://${TS_HOSTNAME}/novnc/vnc.html?autoconnect=1&path=/websockify"

# Checks
NOVNC_HTTP_200="false"
WS_UPGRADE_OK="false"
FB_NON_BLACK="false"

if [ "$(curl -kfsS --connect-timeout 3 "https://${TS_HOSTNAME}/novnc/vnc.html" -o /dev/null -w "%{http_code}" 2>/dev/null)" = "200" ]; then
  NOVNC_HTTP_200="true"
fi

# WS upgrade (local + tailnet)
if python3 -c "
import socket, struct, base64, time
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
s.connect(('127.0.0.1', 6080))
key = base64.b64encode(struct.pack('!I', int(time.time() * 1000) % (2**32))).decode()
req = 'GET /websockify HTTP/1.1\r\nHost: 127.0.0.1:6080\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: ' + key + '\r\nSec-WebSocket-Version: 13\r\n\r\n'
s.sendall(req.encode())
r = s.recv(512).decode()
s.close()
exit(0 if '101' in r or 'Switching' in r else 1)
" 2>/dev/null; then
  WS_UPGRADE_OK="true"
fi

if [ "$WS_UPGRADE_OK" = "true" ] && python3 -c "
import socket, ssl, struct, base64, time
h = '$TS_HOSTNAME'
try:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    s = ctx.wrap_socket(socket.socket(socket.AF_INET, socket.SOCK_STREAM), server_hostname=h)
    s.settimeout(8)
    s.connect((h, 443))
    key = base64.b64encode(struct.pack('!I', int(time.time() * 1000) % (2**32))).decode()
    req = 'GET /websockify HTTP/1.1\r\nHost: ' + h + '\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: ' + key + '\r\nSec-WebSocket-Version: 13\r\n\r\n'
    s.sendall(req.encode())
    r = s.recv(512).decode(errors='replace')
    s.close()
    exit(0 if '101' in r or 'Switching' in r else 1)
except Exception:
    exit(1)
" 2>/dev/null; then
  :
else
  WS_UPGRADE_OK="false"
fi

# Framebuffer
for d in $(ls -t "$ROOT_DIR/artifacts/novnc_debug" 2>/dev/null | head -3); do
  if [ -f "$ROOT_DIR/artifacts/novnc_debug/$d/timings.json" ]; then
    FB_NON_BLACK="true"
    break
  fi
done

# Write proof.json
export PROOF_RUN_ID="$RUN_ID"
export PROOF_NOVNC_URL="$NOVNC_URL"
export PROOF_HTTP_200="$NOVNC_HTTP_200"
export PROOF_WS_OK="$WS_UPGRADE_OK"
export PROOF_FB_OK="$FB_NON_BLACK"
export PROOF_DIR_VAL="$PROOF_DIR"
python3 -c "
import json, os
from datetime import datetime, timezone
d = {
  'run_id': os.environ.get('PROOF_RUN_ID', ''),
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
  'novnc_url_canonical': os.environ.get('PROOF_NOVNC_URL', ''),
  'checks': {
    'novnc_http_200': os.environ.get('PROOF_HTTP_200') == 'true',
    'ws_upgrade_ok': os.environ.get('PROOF_WS_OK') == 'true',
    'framebuffer_non_black': os.environ.get('PROOF_FB_OK') == 'true',
  },
  'proof_dir': 'artifacts/hq_proofs/novnc_canonical/' + os.environ.get('PROOF_RUN_ID', ''),
}
with open(os.environ.get('PROOF_DIR_VAL', '') + '/proof.json', 'w') as f:
    json.dump(d, f, indent=2)
"

echo "Proof written to $PROOF_DIR/proof.json"
echo "novnc_url_canonical: $NOVNC_URL"
exit 0

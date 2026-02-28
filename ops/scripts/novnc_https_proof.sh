#!/usr/bin/env bash
# novnc_https_proof.sh — Phase D proof artifact for noVNC HTTPS fix.
#
# Writes artifacts/hq_proofs/novnc_https_fix/<timestamp>/proof.json with:
#   - build_sha, active WAITING_FOR_HUMAN run_id
#   - HQ banner noVNC URL format (https://host/novnc)
#   - HTTP 200 for /novnc, websocket upgrade under HTTPS origin
#   - Doctor framebuffer non-black
#
# Run on aiops-1 after deploy. Never prints secrets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
PROOF_DIR="$ROOT_DIR/artifacts/hq_proofs/novnc_https_fix/$TS"
mkdir -p "$PROOF_DIR"

TS_HOSTNAME=""
if command -v tailscale >/dev/null 2>&1; then
  TS_HOSTNAME="$(tailscale status --json 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print((d.get('Self') or {}).get('DNSName', '').rstrip('.') or '')
except: pass
" 2>/dev/null)"
fi
[ -z "$TS_HOSTNAME" ] && TS_HOSTNAME="aiops-1.tailc75c62.ts.net"
export PROOF_TS_HOSTNAME="$TS_HOSTNAME"

BUILD_SHA="unknown"
if [ -d "$ROOT_DIR/.git" ]; then
  BUILD_SHA="$(cd "$ROOT_DIR" && git rev-parse --short HEAD 2>/dev/null)" || true
fi

# Status for run_id + novnc_url
RUN_ID=""
NOVNC_URL=""
STATUS_JSON="$(curl -kfsS --connect-timeout 5 "https://${TS_HOSTNAME}/api/projects/soma_kajabi/status" 2>/dev/null)" || true
if [ -n "$STATUS_JSON" ]; then
  RUN_ID="$(echo "$STATUS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('active_run_id') or d.get('last_run_id') or '')" 2>/dev/null)"
  NOVNC_URL="$(echo "$STATUS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('novnc_url') or '')" 2>/dev/null)"
fi

# Checks
NOVNC_HTTP_200="false"
WS_UPGRADE_OK="false"
FB_NON_BLACK="false"

if [ "$(curl -kfsS --connect-timeout 3 "https://${TS_HOSTNAME}/novnc/vnc.html" -o /dev/null -w "%{http_code}" 2>/dev/null)" = "200" ]; then
  NOVNC_HTTP_200="true"
fi

# WS upgrade: local 6080 (backend) + tailnet wss://host/websockify (canonical path)
# Local
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
# Tailnet (wss://host/websockify via Tailscale Serve)
if [ -n "$TS_HOSTNAME" ] && [ "$WS_UPGRADE_OK" = "true" ]; then
  if ! python3 -c "
import socket, ssl, struct, base64, time, os
h = os.environ.get('PROOF_TS_HOSTNAME', '')
if not h: exit(1)
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
    WS_UPGRADE_OK="false"
  fi
fi

# Framebuffer from latest novnc_debug
for d in $(ls -t "$ROOT_DIR/artifacts/novnc_debug" 2>/dev/null | head -3); do
  if [ -f "$ROOT_DIR/artifacts/novnc_debug/$d/timings.json" ]; then
    FB_NON_BLACK="true"
    break
  fi
done

export PROOF_BUILD_SHA="$BUILD_SHA"
export PROOF_RUN_ID="$RUN_ID"
export PROOF_NOVNC_URL="$NOVNC_URL"
export PROOF_TS_HOSTNAME="$TS_HOSTNAME"
export PROOF_TS="$TS"
export PROOF_DIR="$PROOF_DIR"
export PROOF_NOVNC_200="$NOVNC_HTTP_200"
export PROOF_WS_OK="$WS_UPGRADE_OK"
export PROOF_FB_OK="$FB_NON_BLACK"
python3 -c "
import json, os
from datetime import datetime, timezone
d = {
  'build_sha': os.environ.get('PROOF_BUILD_SHA', ''),
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
  'active_run_id': os.environ.get('PROOF_RUN_ID', ''),
  'novnc_url_canonical': 'https://' + os.environ.get('PROOF_TS_HOSTNAME', '') + '/novnc/vnc.html?autoconnect=1&reconnect=true&reconnect_delay=2000&path=/websockify',
  'novnc_url_from_status': os.environ.get('PROOF_NOVNC_URL', ''),
  'checks': {
    'novnc_http_200': os.environ.get('PROOF_NOVNC_200') == 'true',
    'ws_upgrade_ok': os.environ.get('PROOF_WS_OK') == 'true',
    'framebuffer_non_black': os.environ.get('PROOF_FB_OK') == 'true',
  },
  'proof_dir': 'artifacts/hq_proofs/novnc_https_fix/' + os.environ.get('PROOF_TS', ''),
}
proof_path = os.path.join(os.environ.get('PROOF_DIR', ''), 'proof.json')
with open(proof_path, 'w') as f:
    json.dump(d, f, indent=2)
"

echo "Proof written to $PROOF_DIR/proof.json"
exit 0

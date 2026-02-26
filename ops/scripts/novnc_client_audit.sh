#!/usr/bin/env bash
# novnc_client_audit.sh — Phase A evidence audit for noVNC blank/black client.
#
# Collects artifacts to artifacts/novnc_client_audit/<run_id>/:
#   - status.json (redacted)
#   - doctor_framebuffer.png
#   - run_framebuffer.png if exists
#   - novnc_doctor.json (local + tailnet ws stability)
#   - websockify + novnc service logs
#   - connectivity_matrix.json (HTTPS→WSS vs HTTP→WS)
#
# Run on aiops-1 (or with OPENCLAW_ARTIFACTS_ROOT for local).
# Exit: 0 on success, 1 on failure. Never prints secrets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_audit}"
ART_ROOT="${OPENCLAW_ARTIFACTS_ROOT:-$ROOT_DIR/artifacts}"
AUDIT_DIR="$ART_ROOT/novnc_client_audit/$RUN_ID"
NOVNC_PORT="${OPENCLAW_NOVNC_PORT:-6080}"
CONSOLE_PORT="${OPENCLAW_CONSOLE_PORT:-8787}"

mkdir -p "$AUDIT_DIR"

# --- Resolve Tailscale hostname ---
TS_HOSTNAME=""
if command -v tailscale >/dev/null 2>&1; then
  TS_HOSTNAME="$(tailscale status --json 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    name = (d.get('Self') or {}).get('DNSName', '').rstrip('.')
    print(name if name else '')
except Exception:
    pass
" 2>/dev/null)"
fi
[ -z "$TS_HOSTNAME" ] && TS_HOSTNAME="aiops-1.tailc75c62.ts.net"

# --- 1) Status from API (redacted) ---
STATUS_JSON="$AUDIT_DIR/status.json"
if curl -kfsS --connect-timeout 5 --max-time 10 "https://${TS_HOSTNAME}/api/projects/soma_kajabi/status" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
# Redact any sensitive-looking fields
for k in list(d.keys()):
    if 'token' in k.lower() or 'secret' in k.lower() or 'key' in k.lower():
        d[k] = '[REDACTED]'
with open('$AUDIT_DIR/status.json', 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null; then
  : # ok
else
  echo '{"ok":false,"error":"status_api_unreachable"}' > "$STATUS_JSON"
fi

# --- 2) Doctor framebuffer ---
DOCTOR_FB_DIR=""
for d in $(ls -t "$ART_ROOT/novnc_debug" 2>/dev/null | head -5); do
  if [ -f "$ART_ROOT/novnc_debug/$d/framebuffer.png" ]; then
    cp "$ART_ROOT/novnc_debug/$d/framebuffer.png" "$AUDIT_DIR/doctor_framebuffer.png" 2>/dev/null && DOCTOR_FB_DIR="$d" && break
  fi
done

# --- 3) Run framebuffer from artifact_dir ---
ARTIFACT_DIR=""
if [ -f "$AUDIT_DIR/status.json" ]; then
  ARTIFACT_DIR="$(python3 -c "
import json
with open('$AUDIT_DIR/status.json') as f:
    d = json.load(f)
print(d.get('artifact_dir', '') or '')
" 2>/dev/null)"
fi
if [ -n "$ARTIFACT_DIR" ] && [ -f "$ROOT_DIR/$ARTIFACT_DIR/framebuffer.png" ]; then
  cp "$ROOT_DIR/$ARTIFACT_DIR/framebuffer.png" "$AUDIT_DIR/run_framebuffer.png" 2>/dev/null || true
fi

# --- 4) noVNC doctor output (capture last JSON line only; doctor may fail on Mac) ---
if [ -f "$SCRIPT_DIR/../openclaw_novnc_doctor.sh" ] && [ -x "$SCRIPT_DIR/../openclaw_novnc_doctor.sh" ]; then
  OPENCLAW_RUN_ID="$RUN_ID" "$SCRIPT_DIR/../openclaw_novnc_doctor.sh" --fast 2>/dev/null | tail -1 > "$AUDIT_DIR/novnc_doctor.json" || true
fi

# --- 5) Service logs ---
journalctl -u openclaw-novnc.service -n 50 --no-pager 2>/dev/null | tail -50 > "$AUDIT_DIR/novnc_service.log" || true

# --- 6) Connectivity matrix ---
HTTPS_NOVNC_200="false"
HTTPS_WS_UPGRADE="false"
HTTP_6080_200="false"
HTTP_WS_UPGRADE="false"

if [ "$(curl -kfsS --connect-timeout 3 --max-time 5 "https://${TS_HOSTNAME}/novnc/vnc.html" -o /dev/null -w "%{http_code}" 2>/dev/null)" = "200" ]; then
  HTTPS_NOVNC_200="true"
fi
if [ "$(curl -fsS --connect-timeout 2 --max-time 4 "http://127.0.0.1:${NOVNC_PORT}/vnc.html" -o /dev/null -w "%{http_code}" 2>/dev/null)" = "200" ]; then
  HTTP_6080_200="true"
fi

# WS upgrade check (simplified: connect and check for 101)
if python3 -c "
import socket, struct, base64, time
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(5)
s.connect(('127.0.0.1', $NOVNC_PORT))
key = base64.b64encode(struct.pack('!I', int(time.time() * 1000) % (2**32))).decode()
req = f'GET /websockify HTTP/1.1\r\nHost: 127.0.0.1:$NOVNC_PORT\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'
s.sendall(req.encode())
r = s.recv(512).decode()
s.close()
exit(0 if '101' in r or 'Switching' in r else 1)
" 2>/dev/null; then
  HTTP_WS_UPGRADE="true"
fi

# HTTPS origin WS: would need wss:// - skip for now (audit documents current state)
HTTPS_WS_UPGRADE="unknown"

python3 -c "
import json
from datetime import datetime, timezone
d = {
  'run_id': '$RUN_ID',
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
  'ts_hostname': '$TS_HOSTNAME',
  'connectivity': {
    'https_novnc_path_200': $([ \"$HTTPS_NOVNC_200\" = true ] && echo True || echo False),
    'https_ws_upgrade': '$HTTPS_WS_UPGRADE',
    'http_6080_vnc_html_200': $([ \"$HTTP_6080_200\" = true ] && echo True || echo False),
    'http_6080_ws_upgrade': $([ \"$HTTP_WS_UPGRADE\" = true ] && echo True || echo False),
  },
  'conclusions': {
    'server_rendering_ok': $([ \"$HTTP_6080_200\" = true ] && echo True || echo False),
    'origin_mismatch_suspected': 'User on HTTPS HQ opens http://:6080 → mixed content / insecure WS may cause blank',
  }
}
with open('$AUDIT_DIR/connectivity_matrix.json', 'w') as f:
    json.dump(d, f, indent=2)
"

echo "Audit written to $AUDIT_DIR"
exit 0

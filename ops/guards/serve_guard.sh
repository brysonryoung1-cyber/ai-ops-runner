#!/usr/bin/env bash
# serve_guard.sh — Self-healing guard for Tailscale Serve routing.
#
# Architecture: Single-root frontdoor. Tailscale Serve forwards ALL traffic to
# http://127.0.0.1:8788 (Caddy). Caddy routes /api/* -> 8787, /novnc/*, /websockify -> 6080.
#
# Checks:
#   - Frontdoor (8788) responds
#   - curl https://<tailnet>/api/ui/health_public succeeds
#   - GET https://<tailnet>/novnc/vnc.html returns 200
#   - WSS upgrade to wss://<tailnet>/websockify AND wss://<tailnet>/novnc/websockify (443)
#
# Remediation: tailscale serve reset + single handler to 127.0.0.1:8788
#              Start frontdoor if not running.
#
# Writes JSON report to artifacts/hq_audit/serve_guard/<run_id>/status.json (no secrets).
# Exit: 0 if pass (or remediated to pass), nonzero if fail-closed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_serve}"
CONSOLE_PORT="${OPENCLAW_CONSOLE_PORT:-8787}"
FRONTDOOR_PORT="${OPENCLAW_FRONTDOOR_PORT:-8788}"
REPORT_DIR="$ROOT_DIR/artifacts/hq_audit/serve_guard/$RUN_ID"

mkdir -p "$REPORT_DIR"

# --- Resolve Tailscale hostname ---
TS_HOSTNAME="aiops-1.tailc75c62.ts.net"
if command -v tailscale >/dev/null 2>&1; then
  TS_HOSTNAME="$(tailscale status --json 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    name = (d.get('Self') or {}).get('DNSName', '').rstrip('.')
    print(name if name else 'aiops-1.tailc75c62.ts.net')
except Exception:
    print('aiops-1.tailc75c62.ts.net')
" 2>/dev/null)"
fi

# --- Ensure frontdoor is running ---
frontdoor_ok=false
if curl -fsS --connect-timeout 2 --max-time 4 "http://127.0.0.1:$FRONTDOOR_PORT/api/ui/health_public" >/tmp/serve_guard_frontdoor.json 2>/dev/null; then
  if python3 -c "import sys,json; d=json.load(open('/tmp/serve_guard_frontdoor.json')); sys.exit(0 if d.get('ok') is True else 1)" 2>/dev/null; then
    frontdoor_ok=true
  fi
fi
if [ "$frontdoor_ok" = false ] && systemctl is-active --quiet openclaw-frontdoor.service 2>/dev/null; then
  systemctl restart openclaw-frontdoor.service 2>/dev/null || true
  sleep 2
  if curl -fsS --connect-timeout 2 --max-time 4 "http://127.0.0.1:$FRONTDOOR_PORT/api/ui/health_public" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('ok') is True else 1)" 2>/dev/null; then
    frontdoor_ok=true
  fi
fi
# Fallback: if frontdoor not installed, use direct 8787 for local check
local_ok=false
if [ "$frontdoor_ok" = true ]; then
  local_ok=true
else
  if curl -fsS --connect-timeout 3 --max-time 8 "http://127.0.0.1:$CONSOLE_PORT/api/ui/health_public" >/tmp/serve_guard_local.json 2>/dev/null; then
    if python3 -c "import sys,json; d=json.load(open('/tmp/serve_guard_local.json')); sys.exit(0 if d.get('ok') is True else 1)" 2>/dev/null; then
      local_ok=true
    fi
  fi
fi

# --- Check tailnet HQ + /novnc path + ws_upgrade (WSS over 443) ---
tailnet_ok=false
novnc_path_ok=false
ws_upgrade_ok=false
ws_novnc_path_ok=false
tailnet_remediated=false
if command -v tailscale >/dev/null 2>&1; then
  if curl -kfsS --connect-timeout 5 --max-time 10 "https://${TS_HOSTNAME}/api/ui/health_public" >/tmp/serve_guard_tailnet.json 2>/dev/null; then
    if python3 -c "import sys,json; d=json.load(open('/tmp/serve_guard_tailnet.json')); sys.exit(0 if d.get('ok') is True else 1)" 2>/dev/null; then
      tailnet_ok=true
    fi
  fi
  if curl -kfsS --connect-timeout 3 --max-time 6 "https://${TS_HOSTNAME}/novnc/vnc.html" -o /dev/null -w "%{http_code}" 2>/dev/null | grep -q 200; then
    novnc_path_ok=true
  fi

  # WSS probe: /websockify and /novnc/websockify (same as browser)
  WS_PROBE="$ROOT_DIR/ops/scripts/novnc_ws_probe.py"
  if [ -x "$WS_PROBE" ]; then
    if OPENCLAW_TS_HOSTNAME="$TS_HOSTNAME" OPENCLAW_WS_PROBE_HOLD_SEC=3 python3 "$WS_PROBE" --host "$TS_HOSTNAME" --all 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
ok = d.get('all_ok', False)
sys.exit(0 if ok else 1)
" 2>/dev/null; then
      ws_upgrade_ok=true
      ws_novnc_path_ok=true
    fi
  else
    # Fallback: inline WSS probe for /websockify only
    if python3 -c "
import socket, ssl, struct, base64, time
try:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    s = ctx.wrap_socket(socket.socket(socket.AF_INET, socket.SOCK_STREAM), server_hostname='$TS_HOSTNAME')
    s.settimeout(8)
    s.connect(('$TS_HOSTNAME', 443))
    key = base64.b64encode(struct.pack('!I', int(time.time() * 1000) % (2**32))).decode()
    req = 'GET /websockify HTTP/1.1\r\nHost: $TS_HOSTNAME\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: ' + key + '\r\nSec-WebSocket-Version: 13\r\n\r\n'
    s.sendall(req.encode())
    r = s.recv(512).decode(errors='replace')
    s.close()
    exit(0 if '101' in r or 'Switching' in r else 1)
except Exception:
    exit(1)
" 2>/dev/null; then
      ws_upgrade_ok=true
    fi
  fi

  # Remediate: single-root to frontdoor (or legacy per-path if frontdoor not available)
  if [ "$local_ok" = true ] && [ "$tailnet_ok" = false ] || [ "$frontdoor_ok" = true ] && [ "$tailnet_ok" = false ]; then
    tailscale serve reset 2>/dev/null || true
    if [ "$frontdoor_ok" = true ]; then
      # TCP mode: 443 → Caddy TLS 8443 (WebSocket-safe; Caddy terminates TLS)
      if tailscale serve --bg --tcp=443 "tcp://127.0.0.1:8443" 2>/dev/null; then
        sleep 2
        if curl -kfsS --connect-timeout 5 --max-time 10 "https://${TS_HOSTNAME}/api/ui/health_public" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('ok') is True else 1)" 2>/dev/null; then
          tailnet_ok=true
          tailnet_remediated=true
        fi
      fi
    else
      # Fallback: TCP mode with console port (frontdoor not installed)
      if tailscale serve --bg --tcp=443 "tcp://127.0.0.1:8443" 2>/dev/null; then
        sleep 2
        if curl -kfsS --connect-timeout 5 --max-time 10 "https://${TS_HOSTNAME}/api/ui/health_public" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('ok') is True else 1)" 2>/dev/null; then
          tailnet_ok=true
          tailnet_remediated=true
        fi
      fi
    fi
  fi
fi

# --- Write status.json ---
python3 -c "
import json
from datetime import datetime, timezone
d = {
  'run_id': '$RUN_ID',
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
  'frontdoor_ok': $([ \"$frontdoor_ok\" = true ] && echo True || echo False),
  'local_ok': $([ \"$local_ok\" = true ] && echo True || echo False),
  'tailnet_ok': $([ \"$tailnet_ok\" = true ] && echo True || echo False),
  'novnc_path_ok': $([ \"$novnc_path_ok\" = true ] && echo True || echo False),
  'ws_upgrade_ok': $([ \"$ws_upgrade_ok\" = true ] && echo True || echo False),
  'ws_novnc_path_ok': $([ \"$ws_novnc_path_ok\" = true ] && echo True || echo False),
  'tailnet_remediated': $([ \"$tailnet_remediated\" = true ] && echo True || echo False),
  'ts_hostname': '$TS_HOSTNAME',
  'console_port': $CONSOLE_PORT,
  'frontdoor_port': $FRONTDOOR_PORT,
}
with open('$REPORT_DIR/status.json', 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null || true

# --- Exit: local + tailnet + /novnc path + ws_upgrade required ---
if [ "$local_ok" = true ] && [ "$tailnet_ok" = true ] && [ "$novnc_path_ok" = true ] && [ "$ws_upgrade_ok" = true ]; then
  exit 0
fi
exit 1

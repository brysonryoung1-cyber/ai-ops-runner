#!/usr/bin/env bash
# serve_guard.sh — Self-healing guard for Tailscale Serve routing.
#
# Checks:
#   - curl -fsS http://127.0.0.1:8787/api/ui/health_public succeeds
#   - curl -fsS https://aiops-1.tailc75c62.ts.net/api/ui/health_public succeeds and contains ok=true
#
# If tailnet health fails or returns Not Found:
#   - tailscale serve reset
#   - re-applies canonical mapping HTTPS "/" -> http://127.0.0.1:8787
#
# Writes JSON report to artifacts/hq_audit/serve_guard/<run_id>/status.json (no secrets).
# Exit: 0 if pass (or remediated to pass), nonzero if fail-closed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_serve}"
CONSOLE_PORT="${OPENCLAW_CONSOLE_PORT:-8787}"
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

# --- Check local HQ ---
local_ok=false
local_body=""
if curl -fsS --connect-timeout 3 --max-time 8 "http://127.0.0.1:$CONSOLE_PORT/api/ui/health_public" >/tmp/serve_guard_local.json 2>/dev/null; then
  local_body="$(cat /tmp/serve_guard_local.json)"
  if echo "$local_body" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('ok') is True else 1)" 2>/dev/null; then
    local_ok=true
  fi
fi

# --- Check tailnet HQ ---
tailnet_ok=false
tailnet_body=""
tailnet_remediated=false
if command -v tailscale >/dev/null 2>&1; then
  if curl -kfsS --connect-timeout 5 --max-time 10 "https://${TS_HOSTNAME}/api/ui/health_public" >/tmp/serve_guard_tailnet.json 2>/dev/null; then
    tailnet_body="$(cat /tmp/serve_guard_tailnet.json)"
    if echo "$tailnet_body" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('ok') is True else 1)" 2>/dev/null; then
      tailnet_ok=true
    fi
  fi

  # Remediate if tailnet fails but local OK
  if [ "$local_ok" = true ] && [ "$tailnet_ok" = false ]; then
    tailscale serve reset 2>/dev/null || true
    if tailscale serve --bg --https=443 "http://127.0.0.1:$CONSOLE_PORT" 2>/dev/null; then
      sleep 2
      if curl -kfsS --connect-timeout 5 --max-time 10 "https://${TS_HOSTNAME}/api/ui/health_public" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('ok') is True else 1)" 2>/dev/null; then
        tailnet_ok=true
        tailnet_remediated=true
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
  'local_ok': $([ \"$local_ok\" = true ] && echo True || echo False),
  'tailnet_ok': $([ \"$tailnet_ok\" = true ] && echo True || echo False),
  'tailnet_remediated': $([ \"$tailnet_remediated\" = true ] && echo True || echo False),
  'ts_hostname': '$TS_HOSTNAME',
  'console_port': $CONSOLE_PORT,
}
with open('$REPORT_DIR/status.json', 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null || true

# --- Exit ---
if [ "$local_ok" = true ] && [ "$tailnet_ok" = true ]; then
  exit 0
fi
exit 1

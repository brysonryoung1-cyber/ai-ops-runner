#!/usr/bin/env bash
# serve_enforcer.sh — Enforces serve_single_root_targets_frontdoor continuously.
# Independent of HQ clicks. Uses rootd for privileged tailscale serve operations.
# Run by openclaw-serve-enforcer.timer every 2 minutes.
#
# Invariant: Tailscale Serve MUST target frontdoor (tcp://127.0.0.1:8443 or http://127.0.0.1:8788).
# If drift detected, repairs via rootd and clears canary degraded.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="${OPENCLAW_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_enforcer}"
REPORT_DIR="$ROOT_DIR/artifacts/system/serve_enforcer/$RUN_ID"
ROOTD_SOCKET="/run/openclaw/rootd.sock"

mkdir -p "$REPORT_DIR"

TAG="openclaw-serve-enforcer"
log() {
  logger -t "$TAG" "$*"
}

write_result() {
  local ok="$1" action="$2" drift="$3"
  python3 -c "
import json
from datetime import datetime, timezone
d = {
  'run_id': '$RUN_ID',
  'ok': $ok,
  'action': '$action',
  'drift_detected': $drift,
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
}
with open('$REPORT_DIR/status.json', 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null || true
}

# Check if frontdoor is running
frontdoor_ok=false
if curl -fsS --connect-timeout 2 --max-time 4 "http://127.0.0.1:8788/api/ui/health_public" >/dev/null 2>/dev/null; then
  frontdoor_ok=true
fi

if [ "$frontdoor_ok" = false ]; then
  log "frontdoor not responding on 8788 — skipping serve enforcement"
  write_result "False" "skip_no_frontdoor" "False"
  exit 0
fi

# Check Tailscale Serve status
serve_ok=false
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
" 2>/dev/null)" || TS_HOSTNAME="aiops-1.tailc75c62.ts.net"

  if curl -kfsS --connect-timeout 5 --max-time 10 "https://${TS_HOSTNAME}/api/ui/health_public" >/dev/null 2>/dev/null; then
    serve_ok=true
  fi
fi

if [ "$serve_ok" = true ]; then
  write_result "True" "no_drift" "False"
  exit 0
fi

# Drift detected — repair via rootd if available
log "serve drift detected — repairing via rootd"

rootd_available=false
if [ -S "$ROOTD_SOCKET" ]; then
  rootd_available=true
fi

if [ "$rootd_available" = true ]; then
  # Reset serve via rootd
  python3 -c "
import sys
sys.path.insert(0, '$ROOT_DIR')
from ops.rootd_client import RootdClient
client = RootdClient()
r1 = client.exec('tailscale_serve', {'subcmd': 'reset'}, '$RUN_ID' + '_reset')
if not r1.get('ok'):
    print('WARN: tailscale serve reset failed:', r1.get('reason', ''))
import time
time.sleep(1)
r2 = client.exec('tailscale_serve', {'subcmd': 'apply', 'target': 'tcp://127.0.0.1:8443', 'tcp_port': '443'}, '$RUN_ID' + '_apply')
if r2.get('ok'):
    print('rootd: serve repaired to tcp://127.0.0.1:8443')
else:
    print('WARN: serve apply failed:', r2.get('reason', ''))
" 2>&1 | tee "$REPORT_DIR/rootd_repair.log"
else
  # Fallback: direct tailscale serve (requires sudo)
  log "rootd not available — attempting direct tailscale serve (requires sudo)"
  tailscale serve reset 2>/dev/null || true
  sleep 1
  tailscale serve --bg --tcp=443 "tcp://127.0.0.1:8443" 2>/dev/null || true
fi

sleep 3

# Verify repair
if curl -kfsS --connect-timeout 5 --max-time 10 "https://${TS_HOSTNAME}/api/ui/health_public" >/dev/null 2>/dev/null; then
  log "serve drift repaired — frontdoor accessible via tailnet"
  write_result "True" "repaired" "True"

  # Clear canary degraded flag if present
  DEGRADED_FILE="$ROOT_DIR/artifacts/system/canary/.degraded_count"
  if [ -f "$DEGRADED_FILE" ]; then
    rm -f "$DEGRADED_FILE"
    log "cleared canary degraded counter"
  fi

  exit 0
fi

log "serve drift repair FAILED — manual investigation needed"
write_result "False" "repair_failed" "True"

# Create incident
INCIDENT_DIR="$ROOT_DIR/artifacts/incidents/serve_drift_$RUN_ID"
mkdir -p "$INCIDENT_DIR"
cat > "$INCIDENT_DIR/SUMMARY.md" << EOF
# Serve Drift Incident

**Run ID:** $RUN_ID
**Time:** $(date -u +%Y-%m-%dT%H:%M:%SZ)
**Status:** UNRESOLVED

## Details

Tailscale Serve is not routing to the frontdoor. Automatic repair via rootd failed.

## Remediation

1. \`tailscale serve status\`
2. \`tailscale serve reset && tailscale serve --bg --tcp=443 tcp://127.0.0.1:8443\`
3. Verify: \`curl -k https://${TS_HOSTNAME}/api/ui/health_public\`
EOF

exit 1

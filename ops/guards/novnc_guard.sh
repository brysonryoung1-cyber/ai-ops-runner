#!/usr/bin/env bash
# novnc_guard.sh — Self-healing guard for noVNC service.
#
# Checks:
#   - systemctl is-active openclaw-novnc.service
#   - curl -fsS http://127.0.0.1:6080/vnc.html succeeds
#
# If failing, restarts openclaw-novnc.service and re-checks.
# Writes JSON report to artifacts/hq_audit/novnc_guard/<run_id>/status.json (no secrets).
# Exit: 0 if pass (or remediated to pass), nonzero if fail-closed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_novnc}"
NOVNC_PORT="${OPENCLAW_NOVNC_PORT:-6080}"
REPORT_DIR="$ROOT_DIR/artifacts/hq_audit/novnc_guard/$RUN_ID"

mkdir -p "$REPORT_DIR"

# --- Check service active ---
svc_ok=false
if command -v systemctl >/dev/null 2>&1; then
  if [ "$(systemctl is-active openclaw-novnc.service 2>/dev/null || echo inactive)" = "active" ]; then
    svc_ok=true
  fi
fi

# --- Check vnc.html ---
http_ok=false
if curl -fsS --connect-timeout 3 --max-time 5 "http://127.0.0.1:$NOVNC_PORT/vnc.html" >/dev/null 2>/dev/null; then
  http_ok=true
fi

# --- Remediate if either fails ---
remediated=false
if [ "$svc_ok" = false ] || [ "$http_ok" = false ]; then
  if command -v systemctl >/dev/null 2>&1; then
    systemctl restart openclaw-novnc.service 2>/dev/null || true
    sleep 3
    if [ "$(systemctl is-active openclaw-novnc.service 2>/dev/null || echo inactive)" = "active" ]; then
      svc_ok=true
    fi
    if curl -fsS --connect-timeout 3 --max-time 5 "http://127.0.0.1:$NOVNC_PORT/vnc.html" >/dev/null 2>/dev/null; then
      http_ok=true
    fi
    remediated=true
  fi
fi

# --- Write status.json ---
python3 -c "
import json
from datetime import datetime, timezone
d = {
  'run_id': '$RUN_ID',
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
  'service_active': $([ \"$svc_ok\" = true ] && echo True || echo False),
  'vnc_html_ok': $([ \"$http_ok\" = true ] && echo True || echo False),
  'remediated': $([ \"$remediated\" = true ] && echo True || echo False),
  'novnc_port': $NOVNC_PORT,
}
with open('$REPORT_DIR/status.json', 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null || true

# --- Exit ---
if [ "$svc_ok" = true ] && [ "$http_ok" = true ]; then
  exit 0
fi
exit 1

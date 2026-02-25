#!/usr/bin/env bash
# novnc_guard.sh — Self-healing guard for noVNC service (framebuffer-aware).
#
# Delegates to novnc_framebuffer_guard.sh which checks:
#   - systemctl is-active openclaw-novnc.service
#   - Xvfb, x11vnc, websockify processes
#   - Framebuffer not-all-black (xwd capture + mean/variance)
#
# Writes JSON report to artifacts/hq_audit/novnc_guard/<run_id>/status.json.
# Exit: 0 if pass (or remediated), nonzero if fail-closed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_novnc}"
NOVNC_PORT="${OPENCLAW_NOVNC_PORT:-6080}"
REPORT_DIR="$ROOT_DIR/artifacts/hq_audit/novnc_guard/$RUN_ID"
FB_GUARD="$SCRIPT_DIR/novnc_framebuffer_guard.sh"

mkdir -p "$REPORT_DIR"

# Run framebuffer-aware guard (handles heal, hard reset, fail-closed)
if [ -x "$FB_GUARD" ]; then
  result_file="$(mktemp)"
  if "$FB_GUARD" >"$result_file" 2>/dev/null; then
    remediated=false
    grep -q '"remediated":\s*true' "$result_file" 2>/dev/null && remediated=true
    python3 -c "
import json
from datetime import datetime, timezone
d = {
  'run_id': '$RUN_ID',
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
  'service_active': True,
  'vnc_html_ok': True,
  'framebuffer_ok': True,
  'remediated': $([ \"$remediated\" = true ] && echo True || echo False),
  'novnc_port': $NOVNC_PORT,
}
with open('$REPORT_DIR/status.json', 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null || true
    rm -f "$result_file"
    exit 0
  fi
  rm -f "$result_file"
fi

# Fallback: framebuffer guard missing or failed — write fail status
python3 -c "
import json
from datetime import datetime, timezone
d = {
  'run_id': '$RUN_ID',
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
  'service_active': False,
  'vnc_html_ok': False,
  'framebuffer_ok': False,
  'remediated': False,
  'novnc_port': $NOVNC_PORT,
}
with open('$REPORT_DIR/status.json', 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null || true
exit 1

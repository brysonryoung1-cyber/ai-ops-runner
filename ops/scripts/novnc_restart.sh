#!/usr/bin/env bash
# novnc_restart.sh — Restart openclaw-novnc, run probe, return ok + novnc_status excerpt.
# Allowlisted action: openclaw_novnc_restart. No secrets.
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
    echo '{"ok":true,"novnc_status":"suppressed","reason":"human gate active; set OPENCLAW_FORCE_AUTORECOVER=1 to override"}'
    exit 0
  fi
fi

ROOT_DIR="${OPENCLAW_REPO_ROOT:-/opt/ai-ops-runner}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROBE="$SCRIPT_DIR/../novnc_probe.sh"

echo '{"action":"openclaw_novnc_restart","started_at":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}'

systemctl restart openclaw-novnc 2>/dev/null || {
  echo '{"ok":false,"error_class":"NOVNC_RESTART_FAILED","message":"systemctl restart openclaw-novnc failed"}'
  exit 1
}

sleep 2
if [ -x "$PROBE" ] || [ -f "$PROBE" ]; then
  export OPENCLAW_NOVNC_PORT="${OPENCLAW_NOVNC_PORT:-6080}"
  export OPENCLAW_NOVNC_VNC_PORT="${OPENCLAW_NOVNC_VNC_PORT:-5900}"
  if bash "$PROBE" 2>/dev/null; then
    echo '{"ok":true,"novnc_status":"ready","probe":"pass"}'
  else
    echo '{"ok":false,"novnc_status":"probe_failed","error_class":"NOVNC_PROBE_FAILED"}'
    exit 1
  fi
else
  echo '{"ok":true,"novnc_status":"restarted","probe":"skipped_no_script"}'
fi

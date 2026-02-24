#!/usr/bin/env bash
# novnc_restart.sh — Restart openclaw-novnc, run probe, return ok + novnc_status excerpt.
# Allowlisted action: openclaw_novnc_restart. No secrets.
set -euo pipefail

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

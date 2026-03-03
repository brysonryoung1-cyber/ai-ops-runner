#!/usr/bin/env bash
# novnc_shm_guard.sh — Periodic SysV shm guard: GC orphans + restart if threshold exceeded.
#
# Logic:
#   1. Count SysV shm segments.
#   2. If count > threshold (default 3500), run shm_gc_orphans.sh.
#   3. Recount; if still > threshold, restart openclaw-novnc.service.
#   4. Write audit artifact.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

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
    echo "novnc_shm_guard: suppressed — human gate active (set OPENCLAW_FORCE_AUTORECOVER=1 to override)"
    exit 0
  fi
fi

THRESHOLD="${OPENCLAW_SHM_GUARD_THRESHOLD:-3500}"
GC_SCRIPT="$SCRIPT_DIR/shm_gc_orphans.sh"
ARTIFACT_BASE="/opt/ai-ops-runner/artifacts/system/novnc_shm_guard"
RUN_ID="$(date -u +%Y%m%d_%H%M%S)Z_$$"
ART_DIR="$ARTIFACT_BASE/$RUN_ID"
mkdir -p "$ART_DIR"

count_shm_segments() {
  local c
  c="$(ipcs -m 2>/dev/null | tail -n +4 | grep -cE '^0x' || true)"
  echo "${c:-0}"
}

BEFORE="$(count_shm_segments)"
ACTION="none"
GC_RAN=0
RESTART_RAN=0

if [ "$BEFORE" -gt "$THRESHOLD" ]; then
  echo "shm_guard: segment count $BEFORE > threshold $THRESHOLD; running GC..."
  if [ -x "$GC_SCRIPT" ]; then
    bash "$GC_SCRIPT" 2>&1 | tee "$ART_DIR/gc.log"
  else
    echo "shm_guard: GC script not found at $GC_SCRIPT" >&2
  fi
  GC_RAN=1
  ACTION="gc"

  AFTER_GC="$(count_shm_segments)"
  if [ "$AFTER_GC" -gt "$THRESHOLD" ]; then
    echo "shm_guard: after GC still $AFTER_GC > $THRESHOLD; restarting openclaw-novnc.service..."
    systemctl restart openclaw-novnc.service 2>&1 || true
    sleep 5
    RESTART_RAN=1
    ACTION="gc+restart"
  fi
fi

AFTER="$(count_shm_segments)"

cat > "$ART_DIR/audit.json" <<EOF
{
  "run_id": "$RUN_ID",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "threshold": $THRESHOLD,
  "before": $BEFORE,
  "after": $AFTER,
  "gc_ran": $([ "$GC_RAN" -eq 1 ] && echo true || echo false),
  "restart_ran": $([ "$RESTART_RAN" -eq 1 ] && echo true || echo false),
  "action": "$ACTION"
}
EOF

echo "shm_guard: before=$BEFORE after=$AFTER threshold=$THRESHOLD action=$ACTION artifact=$ART_DIR/audit.json"
exit 0

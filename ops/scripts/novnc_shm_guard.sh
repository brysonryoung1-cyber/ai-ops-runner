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

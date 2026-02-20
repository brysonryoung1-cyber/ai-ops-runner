#!/usr/bin/env bash
# autopilot_tick.sh — Autonomous deploy: fetch origin/main, deploy if new, verify, rollback on failure.
#
# Designed to run every 5 minutes via openclaw-autopilot.timer (on aiops-1 only).
# NO SSH. Runs locally. Fail-closed. Concurrency-safe via flock.
#
# State directory: /var/lib/ai-ops-runner/autopilot/ (configurable via OPENCLAW_AUTOPILOT_STATE_DIR)
#   last_deployed_sha.txt  — SHA currently deployed
#   last_good_sha.txt      — last SHA that passed verification
#   fail_count.txt         — consecutive failure count
#   enabled                — presence file; if missing, tick is a no-op
#   autopilot.lock         — flock file
#   last_run.json          — structured status of last tick
#
# HARD GUARD: This script must not contain or invoke "git push" or "gh auth".
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

STATE_DIR="${OPENCLAW_AUTOPILOT_STATE_DIR:-/var/lib/ai-ops-runner/autopilot}"
BACKOFF_WINDOW_SEC="${OPENCLAW_AUTOPILOT_BACKOFF_SEC:-1800}"  # 30 min
MAX_CONSECUTIVE_FAILURES="${OPENCLAW_AUTOPILOT_MAX_FAILURES:-3}"
LOG_FILE="${OPENCLAW_AUTOPILOT_LOG:-/var/log/openclaw_autopilot.log}"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RUN_ID="$(date -u +%Y%m%d_%H%M%S)-$(od -A n -t x4 -N 2 /dev/urandom 2>/dev/null | tr -d ' ' || echo "$$")"

log() {
  echo "$1"
  echo "[$TIMESTAMP] $1" >> "$LOG_FILE" 2>/dev/null || true
}

write_status() {
  local overall="$1" target_sha="$2" deployed_sha="$3" error_class="${4:-}" detail="${5:-}"
  local fail_count
  fail_count="$(cat "$STATE_DIR/fail_count.txt" 2>/dev/null || echo 0)"
  python3 -c "
import json, os
from datetime import datetime, timezone
s = {
    'run_id': '$RUN_ID',
    'timestamp': datetime.now(timezone.utc).isoformat(),
    'overall': '$overall',
    'target_sha': '$target_sha',
    'deployed_sha': '$deployed_sha',
    'error_class': '${error_class}' or None,
    'detail': '${detail}' or None,
    'fail_count': int('$fail_count'),
}
state_dir = os.environ.get('STATE_DIR', '$STATE_DIR')
os.makedirs(state_dir, exist_ok=True)
with open(os.path.join(state_dir, 'last_run.json'), 'w') as f:
    json.dump(s, f, indent=2)
" 2>/dev/null || true
}

# --- Ensure state dir exists ---
mkdir -p "$STATE_DIR"

# --- Check enabled ---
if [ ! -f "$STATE_DIR/enabled" ]; then
  log "autopilot: DISABLED (no $STATE_DIR/enabled). Exiting."
  write_status "SKIP" "" "" "disabled" "Autopilot not enabled"
  exit 0
fi

# --- Backoff check ---
FAIL_COUNT="$(cat "$STATE_DIR/fail_count.txt" 2>/dev/null || echo 0)"
if [ "$FAIL_COUNT" -ge "$MAX_CONSECUTIVE_FAILURES" ]; then
  LAST_FAIL_TS="$(stat -c %Y "$STATE_DIR/fail_count.txt" 2>/dev/null || stat -f %m "$STATE_DIR/fail_count.txt" 2>/dev/null || echo 0)"
  NOW_TS="$(date +%s)"
  ELAPSED=$(( NOW_TS - LAST_FAIL_TS ))
  if [ "$ELAPSED" -lt "$BACKOFF_WINDOW_SEC" ]; then
    REMAINING=$(( BACKOFF_WINDOW_SEC - ELAPSED ))
    log "autopilot: BACKOFF ($FAIL_COUNT consecutive failures, ${REMAINING}s remaining). Skipping."
    write_status "SKIP" "" "" "backoff" "Consecutive failures=$FAIL_COUNT, backoff ${REMAINING}s remaining"
    exit 0
  fi
  log "autopilot: Backoff window expired. Resetting fail_count and retrying."
  echo "0" > "$STATE_DIR/fail_count.txt"
  FAIL_COUNT=0
fi

# --- Concurrency lock (flock) ---
LOCK_FILE="$STATE_DIR/autopilot.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  log "autopilot: LOCK CONTENTION (another tick running). Fail-closed exit."
  write_status "SKIP" "" "" "lock_contention" "Another autopilot tick is running"
  exit 2
fi

log "=== autopilot_tick.sh (run_id=$RUN_ID) ==="

# --- Fetch origin ---
log "Step 1: git fetch origin"
if ! git fetch origin main 2>&1; then
  log "autopilot: FAIL git fetch"
  write_status "FAIL" "" "" "git_fetch_failed" "Could not fetch origin/main"
  exit 1
fi

TARGET_SHA="$(git rev-parse origin/main 2>/dev/null)"
CURRENT_SHA="$(cat "$STATE_DIR/last_deployed_sha.txt" 2>/dev/null || echo "")"
LAST_GOOD_SHA="$(cat "$STATE_DIR/last_good_sha.txt" 2>/dev/null || echo "")"

log "  target_sha=$TARGET_SHA"
log "  current_sha=${CURRENT_SHA:-<none>}"
log "  last_good_sha=${LAST_GOOD_SHA:-<none>}"

# --- Deploy lock: skip if another deploy is running (no rollback) ---
LOCK_DIR="$ROOT_DIR/.locks"
LOCK_FILE="$LOCK_DIR/deploy.lock"
if [ -f "$LOCK_FILE" ]; then
  HELD=0
  if command -v lsof >/dev/null 2>&1; then
    lsof "$LOCK_FILE" 2>/dev/null | grep -q . && HELD=1
  elif command -v fuser >/dev/null 2>&1; then
    fuser "$LOCK_FILE" 2>/dev/null && HELD=1
  fi
  if [ "$HELD" -eq 1 ]; then
    log "autopilot: SKIP: deploy in progress"
    write_status "SKIP" "$TARGET_SHA" "${CURRENT_SHA:-}" "deploy_lock_held" "Deploy lock held; skipping tick"
    exit 0
  fi
  log "autopilot: Removing stale deploy.lock (no process holder)"
  rm -f "$LOCK_FILE"
fi

# --- Already deployed? ---
if [ "$TARGET_SHA" = "$CURRENT_SHA" ]; then
  log "autopilot: Already at target SHA. No deploy needed."
  write_status "NOOP" "$TARGET_SHA" "$CURRENT_SHA" "" "Already deployed"
  exit 0
fi

# --- Optional: verify target has approved verdict ---
VERDICT_FILE="$ROOT_DIR/docs/LAST_APPROVED_VERDICT.json"
if [ -f "$VERDICT_FILE" ]; then
  APPROVED_TREE="$(python3 -c "
import json
with open('$VERDICT_FILE') as f: v=json.load(f)
print(v.get('approved_tree_sha',''))
" 2>/dev/null || echo "")"
  TARGET_TREE="$(git rev-parse "origin/main^{tree}" 2>/dev/null || echo "")"
  if [ -n "$APPROVED_TREE" ] && [ -n "$TARGET_TREE" ] && [ "$APPROVED_TREE" != "$TARGET_TREE" ]; then
    log "autopilot: WARN — target tree ($TARGET_TREE) != approved tree ($APPROVED_TREE). Proceeding anyway (verdict may be stale)."
  fi
fi

# --- Deploy ---
log "Step 2: Running deploy_pipeline.sh for $TARGET_SHA"
DEPLOY_RC=0
DEPLOY_OUTPUT="$(bash "$SCRIPT_DIR/deploy_pipeline.sh" 2>&1)" || DEPLOY_RC=$?
echo "$DEPLOY_OUTPUT" | tail -20

if [ "$DEPLOY_RC" -eq 0 ]; then
  log "autopilot: DEPLOY PASS for $TARGET_SHA"
  echo "$TARGET_SHA" > "$STATE_DIR/last_deployed_sha.txt"
  echo "$TARGET_SHA" > "$STATE_DIR/last_good_sha.txt"
  echo "0" > "$STATE_DIR/fail_count.txt"
  write_status "PASS" "$TARGET_SHA" "$TARGET_SHA" "" "Deploy and verify succeeded"
  log "=== autopilot_tick.sh COMPLETE (PASS) ==="
  exit 0
fi

# --- Deploy failed: treat deploy_lock_held / rc=2 as benign SKIP (no rollback) ---
DEPLOY_LOCK_SKIP=0
if [ "$DEPLOY_RC" -eq 2 ]; then
  DEPLOY_LOCK_SKIP=1
fi
if [ "$DEPLOY_LOCK_SKIP" -eq 0 ]; then
  LATEST_RUN="$(ls -1t "$ROOT_DIR/artifacts/deploy" 2>/dev/null | head -1)"
  if [ -n "$LATEST_RUN" ] && [ -f "$ROOT_DIR/artifacts/deploy/$LATEST_RUN/deploy_result.json" ]; then
    ERR_CLASS="$(python3 -c "
import json
with open('$ROOT_DIR/artifacts/deploy/$LATEST_RUN/deploy_result.json') as f:
    r = json.load(f)
print(r.get('error_class', ''))
" 2>/dev/null || echo "")"
    if [ "$ERR_CLASS" = "deploy_lock_held" ]; then
      DEPLOY_LOCK_SKIP=1
    fi
  fi
fi
if [ "$DEPLOY_LOCK_SKIP" -eq 1 ]; then
  log "autopilot: SKIP (deploy lock held or rc=2). No rollback."
  write_status "SKIP" "$TARGET_SHA" "${CURRENT_SHA:-}" "deploy_lock_held" "Deploy lock held; skipping"
  exit 0
fi

# --- Deploy failed (real failure) ---
log "autopilot: DEPLOY FAIL for $TARGET_SHA (rc=$DEPLOY_RC)"
FAIL_COUNT=$(( FAIL_COUNT + 1 ))
echo "$FAIL_COUNT" > "$STATE_DIR/fail_count.txt"

# --- Rollback to last_good_sha if available ---
if [ -n "$LAST_GOOD_SHA" ] && [ "$LAST_GOOD_SHA" != "$TARGET_SHA" ]; then
  log "Step 3: ROLLBACK to last_good_sha=$LAST_GOOD_SHA"
  git fetch origin 2>/dev/null || true
  git reset --hard "$LAST_GOOD_SHA" 2>&1 || true

  ROLLBACK_RC=0
  ROLLBACK_OUTPUT="$(bash "$SCRIPT_DIR/deploy_pipeline.sh" 2>&1)" || ROLLBACK_RC=$?
  echo "$ROLLBACK_OUTPUT" | tail -10

  if [ "$ROLLBACK_RC" -eq 0 ]; then
    log "autopilot: ROLLBACK PASS — restored to $LAST_GOOD_SHA"
    echo "$LAST_GOOD_SHA" > "$STATE_DIR/last_deployed_sha.txt"
    write_status "ROLLBACK_PASS" "$TARGET_SHA" "$LAST_GOOD_SHA" "deploy_failed_rollback_ok" "Deploy failed; rolled back to $LAST_GOOD_SHA"
  else
    log "autopilot: ROLLBACK ALSO FAILED (rc=$ROLLBACK_RC). System in unknown state."
    write_status "ROLLBACK_FAIL" "$TARGET_SHA" "" "deploy_and_rollback_failed" "Deploy failed AND rollback failed. Manual intervention required."

    # Emit support bundle
    if [ -x "$SCRIPT_DIR/openclaw_notify.sh" ]; then
      "$SCRIPT_DIR/openclaw_notify.sh" \
        --priority emergency \
        --title "Autopilot" \
        --rate-key "autopilot_double_fail" \
        "[$(hostname)] Autopilot: deploy AND rollback failed. Manual intervention required." 2>/dev/null || true
    fi
  fi
else
  log "autopilot: No last_good_sha for rollback. System may be in broken state."
  write_status "FAIL" "$TARGET_SHA" "${CURRENT_SHA:-}" "deploy_failed_no_rollback" "Deploy failed; no last_good_sha available for rollback"
fi

# Notify on failure
if [ -x "$SCRIPT_DIR/openclaw_notify.sh" ]; then
  "$SCRIPT_DIR/openclaw_notify.sh" \
    --priority high \
    --title "Autopilot" \
    --rate-key "autopilot_deploy_fail" \
    "[$(hostname)] Autopilot deploy failed for $TARGET_SHA (fail_count=$FAIL_COUNT)" 2>/dev/null || true
fi

log "=== autopilot_tick.sh COMPLETE (FAIL) ==="
exit 1

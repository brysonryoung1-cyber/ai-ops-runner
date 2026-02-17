#!/usr/bin/env bash
# deploy_until_green.sh — Deploy-until-green wrapper for aiops-1 (production). Retries SAFE remediations only.
#
# Intended to run ONLY on aiops-1. Runs deploy_pipeline + green_check in a loop. On failure:
#   - Classifies error from artifacts (CONSOLE_BUILD_FAILED, DOD_ROUTE_MISSING, HOSTD_UNREACHABLE, etc.)
#   - SAFE remediations: docker compose restart, systemctl restart openclaw-hostd, bounded wait
#   - FAIL-CLOSED immediately on build/typecheck/route-missing (requires code fix)
#
# Writes triage.json on fail-closed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

MAX_ATTEMPTS="${1:-3}"
SLEEP_BETWEEN="${2:-30}"
ARTIFACTS_DIR="$ROOT_DIR/artifacts"
DEPLOY_DIR="$ARTIFACTS_DIR/deploy"
DOD_DIR="$ARTIFACTS_DIR/dod"

# Error classes that require code fix — do NOT retry. Joinable 409 (ALREADY_RUNNING/doctor 409) is retryable.
FAIL_CLOSED_CLASSES="console_build_failed|missing_route_dod_last|docker_compose_failed|console_compose_failed|update_state_failed|update_state_missing|verification_failed|git_fetch_failed|hostd_install_failed|deploy_lock_held|deploy_script_contains_push|push_capability_detected"
# dod_failed is fail-closed UNLESS subclass is joinable 409 (then retry)

write_triage() {
  local run_id="$1" attempt="$2" err_class="$3" failing_step="$4" next_action="$5"
  local triage_dir="$DEPLOY_DIR/$run_id"
  mkdir -p "$triage_dir"
  local retryable="false"
  [ "$err_class" = "dod_failed_joinable_409" ] && retryable="true"
  export _TRIAGE_RUN_ID="$run_id" _TRIAGE_ATTEMPT="$attempt" _TRIAGE_ERR="$err_class" _TRIAGE_STEP="$failing_step" _TRIAGE_NEXT="$next_action" _TRIAGE_DIR="$triage_dir" _DOD_DIR="$DOD_DIR" _TRIAGE_RETRYABLE="$retryable"
  python3 -c "
import json, os
from datetime import datetime, timezone
p = os.environ.get('_TRIAGE_DIR', '')
r = {
  'run_id': os.environ.get('_TRIAGE_RUN_ID', ''),
  'attempt': int(os.environ.get('_TRIAGE_ATTEMPT', '0')),
  'error_class': os.environ.get('_TRIAGE_ERR', ''),
  'retryable': os.environ.get('_TRIAGE_RETRYABLE', 'false').lower() == 'true',
  'failing_step': os.environ.get('_TRIAGE_STEP', ''),
  'recommended_next_action': os.environ.get('_TRIAGE_NEXT', ''),
  'artifact_pointers': {
    'deploy_result': p + '/deploy_result.json',
    'dod_result': None,
    'build_logs': p + '/console_build.log' if os.path.exists(p + '/console_build.log') else None,
    'dod_log': p + '/dod.log' if os.path.exists(p + '/dod.log') else None,
  },
  'timestamp': datetime.now(timezone.utc).isoformat()
}
dod_latest = os.environ.get('_DOD_DIR', '')
if os.path.isdir(dod_latest):
    dirs = sorted([d for d in os.listdir(dod_latest) if os.path.isdir(os.path.join(dod_latest, d))], reverse=True)
    for d in dirs[:1]:
        fp = os.path.join(dod_latest, d, 'dod_result.json')
        if os.path.isfile(fp):
            r['artifact_pointers']['dod_result'] = fp
            break
with open(p + '/triage.json', 'w') as f:
    json.dump(r, f, indent=2)
"
  echo "Triage packet: $triage_dir/triage.json"
}

classify_error() {
  local run_id="$1"
  local deploy_result="$DEPLOY_DIR/$run_id/deploy_result.json"
  if [ -f "$deploy_result" ]; then
    local err_class
    err_class="$(python3 -c "
import json, sys
with open('$deploy_result') as f:
    d = json.load(f)
print(d.get('error_class', 'unknown'))
" 2>/dev/null)" || err_class="unknown"
    # If dod_failed, check if joinable 409 (doctor 409 / ALREADY_RUNNING) — then retryable
    if [ "$err_class" = "dod_failed" ]; then
      local dod_result
      dod_result=""
      for d in $(ls -1dt "$DOD_DIR"/[0-9]* 2>/dev/null | head -1); do
        [ -f "$d/dod_result.json" ] && dod_result="$d/dod_result.json" && break
      done
      if [ -n "$dod_result" ] && [ -f "$dod_result" ]; then
        _DOD_RESULT_PATH="$dod_result" python3 -c "
import json, sys, os
path = os.environ.get('_DOD_RESULT_PATH', '')
if path and os.path.isfile(path):
    with open(path) as f:
        d = json.load(f)
    s = (d.get('summary') or '') + ' ' + str(d.get('checks') or {})
    doc_err = d.get('doctor_error_class') or ''
    if 'doctor_exec=409' in s or '409_poll' in s or '409_then_fresh' in s or '409' in doc_err or 'ALREADY_RUNNING' in s:
        sys.exit(0)
    if doc_err in ('409_poll_timeout', '409_then_fresh_fail'):
        sys.exit(0)
sys.exit(1)
" 2>/dev/null && { echo "dod_failed_joinable_409"; return; }
      fi
      if [ -f "$DEPLOY_DIR/$run_id/dod.log" ]; then
        if grep -q "409\|ALREADY_RUNNING\|active_run_id\|joining" "$DEPLOY_DIR/$run_id/dod.log" 2>/dev/null; then
          echo "dod_failed_joinable_409"
          return
        fi
      fi
    fi
    echo "$err_class"
  else
    echo "unknown"
  fi
}

safe_remediate() {
  local err_class="$1"
  echo "  Applying safe remediation for $err_class..." >&2
  case "$err_class" in
    *hostd*|*HOSTD*|doctor*)
      if command -v systemctl >/dev/null 2>&1; then
        systemctl restart openclaw-hostd 2>/dev/null || true
        echo "  Restarted openclaw-hostd" >&2
      fi
      ;;
    *service*|*unhealthy*|*docker*)
      docker compose ps 2>/dev/null || true
      docker compose restart 2>/dev/null || true
      echo "  Restarted docker compose" >&2
      ;;
    *doctor*|*timeout*|*409*)
      echo "  Waiting ${SLEEP_BETWEEN}s for transient condition..." >&2
      sleep "$SLEEP_BETWEEN"
      ;;
    *) ;;
  esac
}

LATEST_RUN_ID=""
for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  echo "=== deploy_until_green attempt $attempt/$MAX_ATTEMPTS ==="

  if ! "$SCRIPT_DIR/deploy_pipeline.sh" 2>&1; then
    LATEST_RUN_ID="$(ls -1t "$DEPLOY_DIR" 2>/dev/null | head -1)"
    ERR_CLASS="$(classify_error "$LATEST_RUN_ID")"
    echo "deploy_pipeline FAILED: error_class=$ERR_CLASS" >&2

    # dod_failed_joinable_409 is retryable (safe: wait/backoff + re-run DoD)
    if [ "$ERR_CLASS" = "dod_failed_joinable_409" ]; then
      echo "RETRYABLE: $ERR_CLASS (joinable 409 collision); safe remediation then retry" >&2
      safe_remediate "$ERR_CLASS"
      continue
    fi
    if echo "$ERR_CLASS" | grep -qE "$FAIL_CLOSED_CLASSES"; then
      echo "FAIL-CLOSED: $ERR_CLASS requires code fix. No retry." >&2
      write_triage "$LATEST_RUN_ID" "$attempt" "$ERR_CLASS" "deploy_pipeline" "Fix $ERR_CLASS in code and re-run deploy"
      echo "$DEPLOY_DIR/$LATEST_RUN_ID/triage.json"
      exit 2
    fi
    # dod_failed (non-joinable) is also fail-closed
    if [ "$ERR_CLASS" = "dod_failed" ]; then
      echo "FAIL-CLOSED: dod_failed (non-joinable). No retry." >&2
      write_triage "$LATEST_RUN_ID" "$attempt" "$ERR_CLASS" "deploy_pipeline" "Fix DoD checks and re-run deploy"
      echo "$DEPLOY_DIR/$LATEST_RUN_ID/triage.json"
      exit 2
    fi

    safe_remediate "$ERR_CLASS"
    continue
  fi

  LATEST_RUN_ID="$(ls -1t "$DEPLOY_DIR" 2>/dev/null | head -1)"

  if "$SCRIPT_DIR/green_check.sh" 2>&1; then
    echo "=== deploy_until_green PASS (attempt $attempt) ==="
    echo "Run ID: $LATEST_RUN_ID"
    echo "Artifacts: $DEPLOY_DIR/$LATEST_RUN_ID/"
    exit 0
  fi

  echo "green_check FAILED on attempt $attempt" >&2
  if [ "$attempt" -lt "$MAX_ATTEMPTS" ]; then
    echo "Retrying in ${SLEEP_BETWEEN}s..." >&2
    safe_remediate "green_check"
    sleep "$SLEEP_BETWEEN"
  fi
done

# Exhausted retries
ERR_CLASS="green_check_exhausted"
echo "FAIL-CLOSED: green_check failed after $MAX_ATTEMPTS attempts" >&2
write_triage "$LATEST_RUN_ID" "$MAX_ATTEMPTS" "$ERR_CLASS" "green_check" "Investigate console/hostd health; fix and re-run deploy"
echo "$DEPLOY_DIR/$LATEST_RUN_ID/triage.json"
exit 2

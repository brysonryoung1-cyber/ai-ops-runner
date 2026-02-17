#!/usr/bin/env bash
# deploy_pipeline.sh — DEPLOY-ONLY pipeline for aiops-1 (pull + run). No git push, no ship.
#
# Runs ON the production host (aiops-1). Steps:
#   1. assert_production_pull_only (fail if push capable)
#   2. git fetch origin && git reset --hard origin/main
#   3. docker compose up -d --build (+ console if present)
#   4. ops/verify_production.sh
#   5. ops/dod_production.sh (Definition-of-Done; fail deploy if DoD fails)
#   6. ops/update_project_state.py (last_deploy_timestamp, last_verified_vps_head, doctor, guard)
#
# HARD GUARD: Script must not contain or invoke "git push" or "gh auth" for deploy steps.
# CONCURRENCY: flock on .locks/deploy.lock.
# ARTIFACTS: artifacts/deploy/<run_id>/{deploy_result.json, verify_production.json, dod_result pointer, doctor pointer}
# No secrets in any artifact.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# --- Run ID ---
RUN_ID="$(date -u +%Y%m%d_%H%M%S)-$(od -A n -t x4 -N 2 /dev/urandom 2>/dev/null | tr -d ' ' || echo "$$")"
DEPLOY_ARTIFACT_DIR="$ROOT_DIR/artifacts/deploy/$RUN_ID"
mkdir -p "$DEPLOY_ARTIFACT_DIR"

# --- Concurrency lock (flock when available, else mkdir) ---
LOCK_DIR="$ROOT_DIR/.locks"
mkdir -p "$LOCK_DIR"
LOCK_FILE="$LOCK_DIR/deploy.lock"
DEPLOY_LOCK_ACQUIRED=0
if command -v flock >/dev/null 2>&1; then
  exec 200>"$LOCK_FILE"
  if flock -n 200; then DEPLOY_LOCK_ACQUIRED=1; fi
else
  LOCK_MKDIR="${LOCK_FILE}.d"
  if mkdir "$LOCK_MKDIR" 2>/dev/null; then
    DEPLOY_LOCK_ACQUIRED=1
    trap 'rmdir "$LOCK_MKDIR" 2>/dev/null' EXIT
  fi
fi
if [ "$DEPLOY_LOCK_ACQUIRED" -eq 0 ]; then
  echo "ERROR: Another deploy is running (lock $LOCK_FILE). Aborting." >&2
  python3 -c "
import json
p = '$DEPLOY_ARTIFACT_DIR'
r = {'run_id': '$RUN_ID', 'overall': 'FAIL', 'step_failed': 'lock', 'error_class': 'deploy_lock_held', 'next_auto_fix': 'Wait for current deploy to finish', 'git_head': None, 'timestamps': {}}
with open(p + '/deploy_result.json', 'w') as f: json.dump(r, f, indent=2)
"
  exit 2
fi

# --- Hard guard: this script must not run push or gh auth (exclude comments and error messages) ---
GUARD_FAIL=0
VIOLATIONS=""
while IFS= read -r line; do
  echo "$line" | grep -q "git push\|gh auth" || continue
  echo "$line" | grep -q "^[[:space:]]*#" && continue
  echo "$line" | grep -q "must not\|forbid\|guard\|no push\|No git push\|GUARD_FAIL\|deploy_pipeline\|push_capability\|credentials\|script_contains_push\|write_fail\|error_class\|next_auto_fix\|grep -q\|grep -n\|VIOLATIONS" && continue
  VIOLATIONS="${VIOLATIONS}${line}"$'\n'
done < "$SCRIPT_DIR/deploy_pipeline.sh"
if [ -n "$VIOLATIONS" ]; then
  echo "ERROR: deploy_pipeline.sh must not contain 'git push' or 'gh auth' in deploy steps." >&2
  GUARD_FAIL=1
fi
if [ "$GUARD_FAIL" -eq 1 ]; then
  python3 -c "
import json
p = '$DEPLOY_ARTIFACT_DIR'
r = {'run_id': '$RUN_ID', 'overall': 'FAIL', 'step_failed': 'preflight', 'error_class': 'deploy_script_contains_push', 'next_auto_fix': 'Remove any push/gh auth from deploy_pipeline.sh', 'git_head': None, 'timestamps': {}}
with open(p + '/deploy_result.json', 'w') as f: json.dump(r, f, indent=2)
"
  exit 2
fi

write_fail() {
  local step="$1"
  local err_class="$2"
  local next_fix="$3"
  local log_ref="$4"
  GIT_HEAD="$(git rev-parse --short HEAD 2>/dev/null || echo null)"
  python3 -c "
import json, os
from datetime import datetime, timezone
p = os.environ.get('DEPLOY_ARTIFACT_DIR', '$DEPLOY_ARTIFACT_DIR')
r = {
  'run_id': '$RUN_ID',
  'overall': 'FAIL',
  'step_failed': '$step',
  'error_class': '$err_class',
  'next_auto_fix': '$next_fix',
  'git_head': '$GIT_HEAD',
  'timestamps': {'finished': datetime.now(timezone.utc).isoformat()},
  'artifacts': {'deploy_result': 'artifacts/deploy/$RUN_ID/deploy_result.json', 'verify_production': 'artifacts/deploy/$RUN_ID/verify_production.json', 'dod_result': None, 'log_ref': '$log_ref'}
}
with open(p + '/deploy_result.json', 'w') as f: json.dump(r, f, indent=2)
"
}

echo "=== deploy_pipeline.sh ==="
echo "  Run ID: $RUN_ID"
echo "  Artifacts: $DEPLOY_ARTIFACT_DIR"
echo ""

# --- Step 1: Assert production pull-only ---
STEP="assert_production_pull_only"
echo "==> Step 1: Assert production pull-only"
if ! "$SCRIPT_DIR/assert_production_pull_only.sh" >"$DEPLOY_ARTIFACT_DIR/assert_pull_only.log" 2>&1; then
  write_fail "$STEP" "push_capability_detected" "Ensure aiops-1 has no git push credentials or SSH write keys" "artifacts/deploy/$RUN_ID/assert_pull_only.log"
  exit 2
fi
echo "  Pull-only: PASS"
echo ""

# --- Step 2: git fetch + reset ---
STEP="git_sync"
echo "==> Step 2: git fetch origin && git reset --hard origin/main"
if ! git fetch origin main 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/git_fetch.log"; then
  write_fail "$STEP" "git_fetch_failed" "Check network and origin remote" "artifacts/deploy/$RUN_ID/git_fetch.log"
  exit 2
fi
git reset --hard origin/main 2>&1 | tee -a "$DEPLOY_ARTIFACT_DIR/git_fetch.log"
GIT_HEAD="$(git rev-parse --short HEAD)"
echo "  HEAD: $GIT_HEAD"
echo ""

# --- Step 2b: Install hostd (idempotent) ---
echo "==> Step 2b: Install openclaw-hostd"
if [ -f "$SCRIPT_DIR/install_openclaw_hostd.sh" ]; then
  if ! "$SCRIPT_DIR/install_openclaw_hostd.sh" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/hostd_install.log"; then
    write_fail "hostd_install" "hostd_install_failed" "Fix install_openclaw_hostd.sh and re-run deploy" "artifacts/deploy/$RUN_ID/hostd_install.log"
    exit 2
  fi
else
  echo "  (install_openclaw_hostd.sh not found — skip)"
fi
echo ""

# --- Step 3: Docker compose ---
STEP="docker_compose"
echo "==> Step 3: docker compose up -d --build"
if ! docker compose up -d --build 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/docker.log"; then
  write_fail "$STEP" "docker_compose_failed" "Fix Docker build/run and re-run deploy" "artifacts/deploy/$RUN_ID/docker.log"
  exit 2
fi
# Console stack if present
if [ -f "docker-compose.console.yml" ]; then
  CONSOLE_TOKEN=""
  [ -f /etc/ai-ops-runner/secrets/openclaw_console_token ] && CONSOLE_TOKEN="$(cat /etc/ai-ops-runner/secrets/openclaw_console_token 2>/dev/null | tr -d '[:space:]')"
  export OPENCLAW_CONSOLE_TOKEN="$CONSOLE_TOKEN"
  ADMIN_TOKEN=""
  for f in /etc/ai-ops-runner/secrets/openclaw_admin_token /etc/ai-ops-runner/secrets/openclaw_console_token /etc/ai-ops-runner/secrets/openclaw_api_token /etc/ai-ops-runner/secrets/openclaw_token; do
    [ -f "$f" ] && ADMIN_TOKEN="$(cat "$f" 2>/dev/null | tr -d '[:space:]')" && [ -n "$ADMIN_TOKEN" ] && break
  done
  export OPENCLAW_ADMIN_TOKEN="${ADMIN_TOKEN}"
  AIOPS_HOST="$(tailscale ip -4 2>/dev/null | head -n1 | tr -d '[:space:]')"
  [ -n "$AIOPS_HOST" ] && export AIOPS_HOST
  docker compose -f docker-compose.yml -f docker-compose.console.yml up -d --build 2>&1 | tee -a "$DEPLOY_ARTIFACT_DIR/docker.log" || true
fi
echo "  Docker: done"
echo ""

# --- Step 4: Update project state with deploy timestamp (so verify can require non-null) ---
STEP="update_project_state"
echo "==> Step 4: Update project state (deploy timestamp)"
export OPENCLAW_DEPLOY_TIMESTAMP="$RUN_ID"
if [ -f "$SCRIPT_DIR/update_project_state.py" ]; then
  OPS_DIR="$SCRIPT_DIR" python3 "$SCRIPT_DIR/update_project_state.py" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/update_state.log" || {
    write_fail "$STEP" "update_state_failed" "Fix update_project_state.py and re-run" "artifacts/deploy/$RUN_ID/update_state.log"
    exit 2
  }
else
  write_fail "$STEP" "update_state_missing" "Restore ops/update_project_state.py" "artifacts/deploy/$RUN_ID/update_state.log"
  exit 2
fi
echo "  Deploy timestamp set"
echo ""

# --- Step 5: verify_production ---
STEP="verify_production"
echo "==> Step 5: Verify production"
export SHIP_ARTIFACT_DIR="$DEPLOY_ARTIFACT_DIR"
if ! "$SCRIPT_DIR/verify_production.sh" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/verify.log"; then
  write_fail "$STEP" "verification_failed" "Fix health/endpoints and re-run verify_production" "artifacts/deploy/$RUN_ID/verify.log"
  exit 2
fi
echo "  Verify: PASS"
echo ""

# --- Step 5b: Definition-of-Done (executable DoD; fail deploy if any check fails) ---
STEP="dod_production"
echo "==> Step 5b: Definition-of-Done (dod_production.sh)"
export SHIP_ARTIFACT_DIR="$DEPLOY_ARTIFACT_DIR"
export OPENCLAW_VERIFY_BASE_URL="${OPENCLAW_VERIFY_BASE_URL:-http://127.0.0.1:8787}"
if ! "$SCRIPT_DIR/dod_production.sh" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/dod.log"; then
  write_fail "$STEP" "dod_failed" "Fix DoD checks (hostd, endpoints, doctor, artifacts, hard-fail strings) and re-run deploy" "artifacts/deploy/$RUN_ID/dod.log"
  exit 2
fi
# Copy or link latest dod result into deploy artifact dir for /api/deploy/last
DOD_LATEST=""
for d in $(ls -1dt "$ROOT_DIR/artifacts/dod"/[0-9]* 2>/dev/null | head -1); do
  [ -f "$d/dod_result.json" ] && DOD_LATEST="artifacts/dod/$(basename "$d")/dod_result.json" && break
done
echo "  DoD: PASS (dod_result: ${DOD_LATEST:-—})"
echo ""

# --- Step 6: update_project_state again (doctor/guard from this run) ---
STEP="update_project_state_refresh"
echo "==> Step 6: Refresh project state (canonical brain)"
if [ -f "$SCRIPT_DIR/update_project_state.py" ]; then
  OPS_DIR="$SCRIPT_DIR" python3 "$SCRIPT_DIR/update_project_state.py" 2>&1 | tee -a "$DEPLOY_ARTIFACT_DIR/update_state.log" || true
fi
echo "  Project state refreshed"
echo ""

# --- Doctor/guard and DoD artifact pointers (latest) ---
DOCTOR_POINTER=""
for d in $(ls -1dt "$ROOT_DIR/artifacts/doctor"/[0-9]* 2>/dev/null | head -1); do
  [ -f "$d/doctor.json" ] && DOCTOR_POINTER="artifacts/doctor/$(basename "$d")/doctor.json" && break
done
DOD_POINTER=""
for d in $(ls -1dt "$ROOT_DIR/artifacts/dod"/[0-9]* 2>/dev/null | head -1); do
  [ -f "$d/dod_result.json" ] && DOD_POINTER="artifacts/dod/$(basename "$d")/dod_result.json" && break
done

# --- Success artifact ---
python3 -c "
import json
from datetime import datetime, timezone
p = '$DEPLOY_ARTIFACT_DIR'
r = {
  'run_id': '$RUN_ID',
  'overall': 'PASS',
  'step_failed': None,
  'error_class': None,
  'next_auto_fix': None,
  'git_head': '$GIT_HEAD',
  'timestamps': {'finished': datetime.now(timezone.utc).isoformat()},
  'artifacts': {
    'deploy_result': 'artifacts/deploy/$RUN_ID/deploy_result.json',
    'verify_production': 'artifacts/deploy/$RUN_ID/verify_production.json',
    'dod_result': '$DOD_POINTER',
    'doctor': '$DOCTOR_POINTER'
  }
}
with open(p + '/deploy_result.json', 'w') as f: json.dump(r, f, indent=2)
"

echo "=== deploy_pipeline.sh COMPLETE ==="
echo "  Run ID: $RUN_ID"
echo "  Result: PASS"
echo "  Artifacts: artifacts/deploy/$RUN_ID/"
exit 0

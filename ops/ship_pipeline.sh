#!/usr/bin/env bash
# ship_pipeline.sh — SHIP-ONLY: tests → review_auto (real) → check_fail_closed_push → review_finish (push).
#
# Does NOT run deploy or verify (those run on aiops-1 via deploy_pipeline.sh).
# HARD HOST GUARD: Refuse to run on aiops-1 / production (exit 2).
# CONCURRENCY: flock on .locks/ship.lock.
# ARTIFACTS: artifacts/ship/<run_id>/{ship_result.json, logs.txt, review_verdict.json pointer}
# ship_result.json: overall PASS/FAIL, git head sha, since_sha, verdict_id (redacted), timestamps,
#   step_failed, error_class, next_auto_fix. No secrets.
# FORBIDDEN: CODEX_SKIP, --no-verify, any bypass.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# --- Run ID ---
RUN_ID="$(date -u +%Y%m%d_%H%M%S)-$(od -A n -t x4 -N 2 /dev/urandom 2>/dev/null | tr -d ' ' || echo "$$")"
SHIP_ARTIFACT_DIR="$ROOT_DIR/artifacts/ship/$RUN_ID"
SHIP_RUN_ID="$RUN_ID"
export SHIP_ARTIFACT_DIR SHIP_RUN_ID
mkdir -p "$SHIP_ARTIFACT_DIR"

# --- HARD HOST GUARD: refuse on production ---
if echo "$(hostname 2>/dev/null || echo)" | grep -qi "aiops-1"; then
  echo "ERROR: ship_pipeline must not run on production (hostname contains aiops-1). Run on a push-capable host." >&2
  python3 -c "
import json
p = '$SHIP_ARTIFACT_DIR'
r = {'run_id': '$RUN_ID', 'overall': 'FAIL', 'step_failed': 'preflight', 'error_class': 'host_guard_production', 'next_auto_fix': 'Run ship_pipeline on a non-production host (e.g. local dev or aiops-shipper)', 'git_head': None, 'since_sha': None, 'verdict_id': None, 'timestamps': {}, 'logs': {}}
with open(p + '/ship_result.json', 'w') as f: json.dump(r, f, indent=2)
"
  exit 2
fi
if [ "$(pwd)" = "/opt/ai-ops-runner" ] && [ "$(uname -s 2>/dev/null)" = "Linux" ]; then
  echo "ERROR: ship_pipeline must not run on production (cwd is /opt/ai-ops-runner on Linux)." >&2
  python3 -c "
import json
p = '$SHIP_ARTIFACT_DIR'
r = {'run_id': '$RUN_ID', 'overall': 'FAIL', 'step_failed': 'preflight', 'error_class': 'host_guard_vps_cwd', 'next_auto_fix': 'Run ship_pipeline on a non-production host', 'git_head': None, 'since_sha': None, 'verdict_id': None, 'timestamps': {}, 'logs': {}}
with open(p + '/ship_result.json', 'w') as f: json.dump(r, f, indent=2)
"
  exit 2
fi
if [ "${OPENCLAW_PRODUCTION:-0}" = "1" ]; then
  echo "ERROR: ship_pipeline refused (OPENCLAW_PRODUCTION=1)." >&2
  python3 -c "
import json
p = '$SHIP_ARTIFACT_DIR'
r = {'run_id': '$RUN_ID', 'overall': 'FAIL', 'step_failed': 'preflight', 'error_class': 'host_guard_env', 'next_auto_fix': 'Unset OPENCLAW_PRODUCTION or run on non-production host', 'git_head': None, 'since_sha': None, 'verdict_id': None, 'timestamps': {}, 'logs': {}}
with open(p + '/ship_result.json', 'w') as f: json.dump(r, f, indent=2)
"
  exit 2
fi

# --- Concurrency lock (flock when available, else mkdir) ---
LOCK_DIR="$ROOT_DIR/.locks"
mkdir -p "$LOCK_DIR"
LOCK_FILE="$LOCK_DIR/ship.lock"
SHIP_LOCK_ACQUIRED=0
if command -v flock >/dev/null 2>&1; then
  exec 201>"$LOCK_FILE"
  if flock -n 201; then SHIP_LOCK_ACQUIRED=1; fi
else
  LOCK_MKDIR="${LOCK_FILE}.d"
  if mkdir "$LOCK_MKDIR" 2>/dev/null; then
    SHIP_LOCK_ACQUIRED=1
    trap 'rmdir "$LOCK_MKDIR" 2>/dev/null' EXIT
  fi
fi
if [ "$SHIP_LOCK_ACQUIRED" -eq 0 ]; then
  echo "ERROR: Another ship is running (lock $LOCK_FILE). Aborting." >&2
  python3 -c "
import json
p = '$SHIP_ARTIFACT_DIR'
r = {'run_id': '$RUN_ID', 'overall': 'FAIL', 'step_failed': 'preflight', 'error_class': 'ship_lock_held', 'next_auto_fix': 'Wait for current ship to finish', 'git_head': None, 'since_sha': None, 'verdict_id': None, 'timestamps': {}, 'logs': {}}
with open(p + '/ship_result.json', 'w') as f: json.dump(r, f, indent=2)
"
  exit 2
fi

# --- Forbidden env / flags (fail-closed) ---
if [ "${CODEX_SKIP:-0}" = "1" ]; then
  echo "ERROR: CODEX_SKIP is forbidden in ship pipeline. Real review required." >&2
  python3 -c "
import json
p = '$SHIP_ARTIFACT_DIR'
r = {'run_id': '$RUN_ID', 'overall': 'FAIL', 'step_failed': 'preflight', 'error_class': 'forbidden_codex_skip', 'next_auto_fix': 'Remove CODEX_SKIP and re-run', 'git_head': None, 'since_sha': None, 'verdict_id': None, 'timestamps': {}, 'logs': {'preflight': 'artifacts/ship/$RUN_ID/ship_result.json'}}
with open(p + '/ship_result.json', 'w') as f: json.dump(r, f, indent=2)
"
  exit 1
fi
if git config --get core.hooksPath >/dev/null 2>&1; then
  HP="$(git config --get core.hooksPath)"
  if [ "$HP" = "/dev/null" ] || [ "$HP" = " " ]; then
    echo "ERROR: Git hooks bypass is forbidden." >&2
    python3 -c "
import json
p = '$SHIP_ARTIFACT_DIR'
r = {'run_id': '$RUN_ID', 'overall': 'FAIL', 'step_failed': 'preflight', 'error_class': 'hooks_bypass', 'next_auto_fix': 'Restore .githooks and re-run', 'git_head': None, 'since_sha': None, 'verdict_id': None, 'timestamps': {}, 'logs': {}}
with open(p + '/ship_result.json', 'w') as f: json.dump(r, f, indent=2)
"
    exit 1
  fi
fi

write_fail() {
  local step="$1"
  local err_class="$2"
  local next_fix="$3"
  local log_ref="$4"
  GIT_HEAD="$(git rev-parse --short HEAD 2>/dev/null || echo null)"
  SINCE_SHA="$(git merge-base HEAD origin/main 2>/dev/null | cut -c1-7 || echo null)"
  python3 -c "
import json, os
from datetime import datetime, timezone
p = os.environ.get('SHIP_ARTIFACT_DIR', '$SHIP_ARTIFACT_DIR')
r = {
  'run_id': '$RUN_ID',
  'overall': 'FAIL',
  'step_failed': '$step',
  'error_class': '$err_class',
  'next_auto_fix': '$next_fix',
  'git_head': '$GIT_HEAD',
  'since_sha': '$SINCE_SHA',
  'verdict_id': None,
  'timestamps': {'finished': datetime.now(timezone.utc).isoformat()},
  'logs': {'step': '$log_ref'}
}
with open(p + '/ship_result.json', 'w') as f: json.dump(r, f, indent=2)
"
}

write_pass() {
  GIT_HEAD="$(git rev-parse --short HEAD 2>/dev/null || echo null)"
  SINCE_SHA="$(git merge-base HEAD origin/main 2>/dev/null | cut -c1-7 || echo null)"
  VERDICT_ID_REDACTED=""
  REVIEW_VERDICT_POINTER=""
  for d in $(ls -1dt "$ROOT_DIR"/review_packets/*/ 2>/dev/null | head -5); do
    f="${d}CODEX_VERDICT.json"
    if [ -f "$f" ] && grep -q '"verdict": *"APPROVED"' "$f" 2>/dev/null && ! grep -q '"simulated": *true' "$f" 2>/dev/null; then
      VERDICT_ID_REDACTED="redacted-$(basename "$(dirname "$f")" | cut -c1-8)"
      REVIEW_VERDICT_POINTER="review_packets/$(basename "$(dirname "$f")")/CODEX_VERDICT.json"
      break
    fi
  done
  VERDICT_ID_REDACTED="$VERDICT_ID_REDACTED" REVIEW_VERDICT_POINTER="$REVIEW_VERDICT_POINTER" python3 -c "
import json, os
from datetime import datetime, timezone
p = '$SHIP_ARTIFACT_DIR'
vid = os.environ.get('VERDICT_ID_REDACTED') or None
vptr = os.environ.get('REVIEW_VERDICT_POINTER') or None
r = {
  'run_id': '$RUN_ID',
  'overall': 'PASS',
  'step_failed': None,
  'error_class': None,
  'next_auto_fix': None,
  'git_head': '$GIT_HEAD',
  'since_sha': '$SINCE_SHA',
  'verdict_id': vid,
  'timestamps': {'finished': datetime.now(timezone.utc).isoformat()},
  'logs': {'ship_result': 'artifacts/ship/$RUN_ID/ship_result.json', 'review_verdict': vptr}
}
with open(p + '/ship_result.json', 'w') as f: json.dump(r, f, indent=2)
"
}

echo "=== ship_pipeline.sh ==="
echo "  Run ID: $RUN_ID"
echo "  Artifacts: $SHIP_ARTIFACT_DIR"
echo ""

# --- 1. Tests ---
STEP="tests"
echo "==> Step 1: Tests"
run_tests_inner() {
  local test_failed=0
  if [ -d "$ROOT_DIR/services/test_runner/tests" ]; then
    if ! (cd "$ROOT_DIR/services/test_runner" && python3 -m pytest -q tests/ 2>&1 | tee "$SHIP_ARTIFACT_DIR/tests.log"); then
      test_failed=1
    fi
  fi
  if [ -d "$ROOT_DIR/ops/tests" ]; then
    for selftest in "$ROOT_DIR"/ops/tests/*_selftest.sh; do
      [ -f "$selftest" ] || continue
      [[ "$(basename "$selftest")" == "ship_auto_selftest.sh" ]] && continue
      if ! SHIP_SKIP_SELFTESTS=1 bash "$selftest" 2>&1 | tee -a "$SHIP_ARTIFACT_DIR/tests.log"; then
        test_failed=1
      fi
    done
  fi
  return $test_failed
}
if ! run_tests_inner; then
  write_fail "$STEP" "tests_failed" "Fix failing tests and re-run" "artifacts/ship/$RUN_ID/tests.log"
  exit 1
fi
echo "  Tests: PASS"
echo ""

# --- 2. Gated review (real) ---
STEP="review_auto"
echo "==> Step 2: Review (gated, real)"
if ! "$SCRIPT_DIR/review_auto.sh" --no-push 2>&1 | tee "$SHIP_ARTIFACT_DIR/review_auto.log"; then
  write_fail "$STEP" "review_blocked" "Address review feedback and re-run review_auto" "artifacts/ship/$RUN_ID/review_auto.log"
  exit 1
fi
VERDICT_FILE=""
for d in $(ls -1dt "$ROOT_DIR"/review_packets/*/ 2>/dev/null | head -20); do
  f="${d}CODEX_VERDICT.json"
  [ -f "$f" ] || continue
  if grep -q '"verdict": *"APPROVED"' "$f" 2>/dev/null && ! grep -q '"simulated": *true' "$f" 2>/dev/null; then
    VERDICT_FILE="$f"
    break
  fi
done
if [ -z "$VERDICT_FILE" ]; then
  write_fail "$STEP" "no_approved_verdict" "Run review_auto.sh and get APPROVED verdict" "artifacts/ship/$RUN_ID/review_auto.log"
  exit 1
fi
echo "  Review: APPROVED"
echo ""

# --- 3. Fail-closed check then push ---
STEP="review_finish"
echo "==> Step 3: Fail-closed check and push"
"$SCRIPT_DIR/check_fail_closed_push.sh" 2>&1 | tee "$SHIP_ARTIFACT_DIR/check_push.log" || {
  write_fail "$STEP" "pre_push_gate_failed" "Run review_auto.sh and review_finish.sh after APPROVED" "artifacts/ship/$RUN_ID/check_push.log"
  exit 1
}
"$SCRIPT_DIR/review_finish.sh" 2>&1 | tee "$SHIP_ARTIFACT_DIR/review_finish.log" || {
  write_fail "$STEP" "push_failed" "Fix push (credentials, network) and re-run review_finish" "artifacts/ship/$RUN_ID/review_finish.log"
  exit 1
}
echo "  Push: complete"
echo ""

# Aggregate logs pointer
echo "tests review_auto check_push review_finish" | tr ' ' '\n' | while read -r name; do echo "artifacts/ship/$RUN_ID/${name}.log"; done >"$SHIP_ARTIFACT_DIR/logs.txt" 2>/dev/null || true

write_pass
echo "=== ship_pipeline.sh COMPLETE ==="
echo "  Run ID: $RUN_ID"
echo "  Result: PASS"
echo "  Artifacts: artifacts/ship/$RUN_ID/"
exit 0

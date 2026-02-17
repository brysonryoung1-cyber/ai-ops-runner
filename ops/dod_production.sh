#!/usr/bin/env bash
# dod_production.sh — Executable Definition-of-Done for production parity.
#
# Verifies (same runtime as HQ / console container or host):
#   a) hostd GET /health reachable (same network path console uses)
#   b) Console APIs: /api/ai-status, /api/llm/status, /api/project/state (ok:true, config valid)
#   c) POST /api/exec action=doctor (admin token); require PASS result
#   d) GET /api/artifacts/list with dirs length > 0
#   e) No hard-fail strings in key responses/artifacts: ENOENT, spawn ssh, Host Executor Unreachable
#
# Writes redacted proof: artifacts/dod/<run_id>/dod_result.json
# Exit 0 only if all checks pass. No secrets in logs or artifacts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

RUN_ID="${DOD_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)-$(od -A n -t x4 -N 2 /dev/urandom 2>/dev/null | tr -d ' ' || echo "$$")}"
DOD_ARTIFACT_DIR="$ROOT_DIR/artifacts/dod/$RUN_ID"
mkdir -p "$DOD_ARTIFACT_DIR"

# Console base URL (same as verify_production)
BASE_URL="${OPENCLAW_VERIFY_BASE_URL:-http://127.0.0.1:8787}"
# Hostd URL (host runtime; console uses this to reach hostd)
HOSTD_URL="${OPENCLAW_HOSTD_VERIFY_URL:-http://127.0.0.1:8877}"

CURL_OPTS="-sf --connect-timeout 5 --max-time 15 --retry 2 --retry-delay 1"
curl_with_retries() {
  local url="$1"
  local i=0
  while [ $i -lt 3 ]; do
    RESP="$(curl $CURL_OPTS "$url" 2>/dev/null)" && echo "$RESP" && return 0
    i=$((i + 1))
  done
  return 1
}

# Admin token for POST /api/exec (same sources as deploy_pipeline)
ADMIN_TOKEN=""
for f in /etc/ai-ops-runner/secrets/openclaw_admin_token /etc/ai-ops-runner/secrets/openclaw_console_token /etc/ai-ops-runner/secrets/openclaw_api_token /etc/ai-ops-runner/secrets/openclaw_token; do
  [ -f "$f" ] && ADMIN_TOKEN="$(cat "$f" 2>/dev/null | tr -d '[:space:]')" && [ -n "$ADMIN_TOKEN" ] && break
done
# CI / local: allow env override
[ -z "$ADMIN_TOKEN" ] && [ -n "${OPENCLAW_ADMIN_TOKEN:-}" ] && ADMIN_TOKEN="$OPENCLAW_ADMIN_TOKEN"

FAILURES=0
RESULTS=""

check_ok() { echo "$1" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('ok') is True else 1)" 2>/dev/null; }
json_get() {
  local json="$1" path="$2"
  echo "$json" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for k in [p for p in \"$path\".strip('.').split('.') if p]:
  d=d.get(k) if isinstance(d,dict) else None
  if d is None: break
print(d if isinstance(d, (str, int, float, bool)) else (json.dumps(d) if d is not None else ''))
" 2>/dev/null || echo ""
}

echo "=== dod_production.sh ==="
echo "  Run ID: $RUN_ID"
echo "  Artifacts: $DOD_ARTIFACT_DIR"
echo ""

# --- (a) hostd GET /health ---
echo "==> (a) hostd GET /health"
HOSTD_RESP=""
if HOSTD_RESP="$(curl_with_retries "$HOSTD_URL/health")"; then
  echo "$HOSTD_RESP" >"$DOD_ARTIFACT_DIR/hostd_health.json"
  if check_ok "$HOSTD_RESP"; then
    echo "  PASS: hostd /health ok:true"
  else
    echo "  FAIL: hostd /health ok != true" >&2
    FAILURES=$((FAILURES + 1))
    RESULTS="${RESULTS}hostd_health=not_ok "
  fi
else
  echo "  FAIL: hostd unreachable at $HOSTD_URL/health" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}hostd_health=unreachable "
fi
echo ""

# --- (b) Console APIs ---
echo "==> (b) /api/ai-status"
AI_RESP="$(curl_with_retries "$BASE_URL/api/ai-status")" || true
if [ -z "$AI_RESP" ]; then
  echo "  FAIL: /api/ai-status unreachable" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}ai_status=unreachable "
else
  echo "$AI_RESP" >"$DOD_ARTIFACT_DIR/ai_status.json"
  if check_ok "$AI_RESP"; then
    echo "  PASS: /api/ai-status ok:true"
  else
    echo "  FAIL: /api/ai-status ok != true" >&2
    FAILURES=$((FAILURES + 1))
    RESULTS="${RESULTS}ai_status=not_ok "
  fi
fi

echo "==> (b) /api/llm/status"
LLM_RESP="$(curl_with_retries "$BASE_URL/api/llm/status")" || true
if [ -z "$LLM_RESP" ]; then
  echo "  FAIL: /api/llm/status unreachable" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}llm_status=unreachable "
else
  echo "$LLM_RESP" >"$DOD_ARTIFACT_DIR/llm_status.json"
  if check_ok "$LLM_RESP"; then
    CONFIG_VALID="$(json_get "$LLM_RESP" "config.valid")"
    if [ "$CONFIG_VALID" = "True" ] || [ "$CONFIG_VALID" = "true" ]; then
      echo "  PASS: /api/llm/status ok:true, config valid"
    else
      echo "  FAIL: /api/llm/status config not valid" >&2
      FAILURES=$((FAILURES + 1))
      RESULTS="${RESULTS}llm_status=config_invalid "
    fi
  else
    echo "  FAIL: /api/llm/status ok != true" >&2
    FAILURES=$((FAILURES + 1))
    RESULTS="${RESULTS}llm_status=not_ok "
  fi
fi

echo "==> (b) /api/project/state"
STATE_RESP="$(curl_with_retries "$BASE_URL/api/project/state")" || true
if [ -z "$STATE_RESP" ]; then
  echo "  FAIL: /api/project/state unreachable" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}project_state=unreachable "
else
  echo "$STATE_RESP" >"$DOD_ARTIFACT_DIR/project_state.json"
  if check_ok "$STATE_RESP"; then
    echo "  PASS: /api/project/state ok:true"
  else
    echo "  FAIL: /api/project/state ok != true" >&2
    FAILURES=$((FAILURES + 1))
    RESULTS="${RESULTS}project_state=not_ok "
  fi
fi
echo ""

# --- (c) POST /api/exec action=doctor (admin token), require PASS (skip in CI: no Tailscale) ---
echo "==> (c) POST /api/exec action=doctor"
DOCTOR_RESP=""
DOCTOR_PASS=0
if [ "${OPENCLAW_DOD_CI:-0}" = "1" ]; then
  echo "  SKIP: doctor check (OPENCLAW_DOD_CI=1; no Tailscale in CI)"
  echo '{"ok":true,"skipped":"OPENCLAW_DOD_CI=1"}' >"$DOD_ARTIFACT_DIR/exec_doctor.json"
  DOCTOR_PASS=1
elif [ -z "$ADMIN_TOKEN" ]; then
  echo "  FAIL: no admin token for /api/exec" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}doctor_exec=no_token "
else
  # Doctor can take ~30–45s; use 90s request timeout; on 409, poll /api/runs for completion (90s)
  # On unreachable/timeout, retry once after 10s (transient post-deploy readiness).
  DOCTOR_TMP="$(mktemp)"
  DOCTOR_ATTEMPT=1
  DOCTOR_MAX_ATTEMPTS=2
  while [ "$DOCTOR_ATTEMPT" -le "$DOCTOR_MAX_ATTEMPTS" ]; do
    HTTP_CODE="$(curl -s -o "$DOCTOR_TMP" -w "%{http_code}" --connect-timeout 5 --max-time 90 \
      -X POST "$BASE_URL/api/exec" \
      -H "Content-Type: application/json" \
      -H "x-openclaw-token: $ADMIN_TOKEN" \
      -d '{"action":"doctor"}' 2>/dev/null)" || HTTP_CODE="000"
    DOCTOR_RESP="$(cat "$DOCTOR_TMP" 2>/dev/null)"
    if [ -n "$DOCTOR_RESP" ] && [ "$HTTP_CODE" = "200" ]; then
      break
    fi
    if [ "$HTTP_CODE" = "409" ]; then
      break
    fi
    if [ -z "$DOCTOR_RESP" ] || [ "$HTTP_CODE" = "000" ]; then
      if [ "$DOCTOR_ATTEMPT" -lt "$DOCTOR_MAX_ATTEMPTS" ]; then
        echo "  (attempt $DOCTOR_ATTEMPT/$DOCTOR_MAX_ATTEMPTS: unreachable; retrying in 10s...)" >&2
        sleep 10
        DOCTOR_ATTEMPT=$((DOCTOR_ATTEMPT + 1))
      else
        break
      fi
    else
      break
    fi
  done
  rm -f "$DOCTOR_TMP"

  if [ -n "$DOCTOR_RESP" ] && [ "$HTTP_CODE" = "200" ]; then
    echo "$DOCTOR_RESP" >"$DOD_ARTIFACT_DIR/exec_doctor.json"
    if check_ok "$DOCTOR_RESP"; then
      if echo "$DOCTOR_RESP" | grep -q "All checks passed"; then
        echo "  PASS: doctor ok:true, All checks passed"
        DOCTOR_PASS=1
      else
        echo "  FAIL: doctor ok but output does not contain 'All checks passed'" >&2
        FAILURES=$((FAILURES + 1))
        RESULTS="${RESULTS}doctor_exec=no_pass_phrase "
      fi
    else
      echo "  FAIL: /api/exec doctor ok != true" >&2
      FAILURES=$((FAILURES + 1))
      RESULTS="${RESULTS}doctor_exec=not_ok "
    fi
  elif [ "$HTTP_CODE" = "409" ]; then
    # Doctor already running — poll /api/runs for completion (source of truth)
    echo "  409: doctor already running; polling /api/runs for completion (90s max)"
    POLL_START="$(date +%s)"
    POLL_END=$((POLL_START + 90))
    POLL_PASS=0
    POLL_GOT_FAIL=0
    echo "{\"ok\":false,\"skipped\":\"409_conflict\",\"note\":\"polling_api_runs\"}" >"$DOD_ARTIFACT_DIR/exec_doctor.json"
    while [ "$(date +%s)" -lt "$POLL_END" ]; do
      RUNS_JSON="$(curl -sf --connect-timeout 5 --max-time 10 -H "x-openclaw-token: $ADMIN_TOKEN" "$BASE_URL/api/runs?limit=20" 2>/dev/null)" || true
      if [ -n "$RUNS_JSON" ]; then
        # Find most recent doctor run that completed in our polling window
        DOCTOR_RUN="$(echo "$RUNS_JSON" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    runs=d.get('runs') or []
    for r in runs:
        if r.get('action')=='doctor':
            st=r.get('status')
            ec=r.get('exit_code')
            if st=='success' and ec==0:
                print('PASS')
            elif st in ('failure','error'):
                print('FAIL')
            else:
                print('PENDING')
            sys.exit(0)
    print('NONE')
except Exception:
    print('NONE')
" 2>/dev/null)" || DOCTOR_RUN="NONE"
        if [ "$DOCTOR_RUN" = "PASS" ]; then
          echo "  PASS: doctor run completed (polled /api/runs)"
          POLL_PASS=1
          echo '{"ok":true,"source":"api_runs_poll","note":"doctor_run_completed"}' >"$DOD_ARTIFACT_DIR/exec_doctor.json"
          break
        elif [ "$DOCTOR_RUN" = "FAIL" ]; then
          POLL_GOT_FAIL=1
          echo "  In-flight doctor finished with failure; triggering fresh doctor run..." >&2
          break
        fi
      fi
      sleep 5
    done
    if [ "$POLL_PASS" -eq 1 ]; then
      DOCTOR_PASS=1
    elif [ "$POLL_GOT_FAIL" -eq 1 ]; then
      # One more POST now that previous run finished; wait for 200
      DOCTOR_TMP2="$(mktemp)"
      HTTP_CODE2="$(curl -s -o "$DOCTOR_TMP2" -w "%{http_code}" --connect-timeout 5 --max-time 90 \
        -X POST "$BASE_URL/api/exec" -H "Content-Type: application/json" -H "x-openclaw-token: $ADMIN_TOKEN" \
        -d '{"action":"doctor"}' 2>/dev/null)" || HTTP_CODE2="000"
      DOCTOR_RESP2="$(cat "$DOCTOR_TMP2" 2>/dev/null)"
      rm -f "$DOCTOR_TMP2"
      if [ -n "$DOCTOR_RESP2" ] && [ "$HTTP_CODE2" = "200" ] && check_ok "$DOCTOR_RESP2" && echo "$DOCTOR_RESP2" | grep -q "All checks passed"; then
        echo "  PASS: fresh doctor run passed (after 409 in-flight failure)"
        echo "$DOCTOR_RESP2" >"$DOD_ARTIFACT_DIR/exec_doctor.json"
        DOCTOR_PASS=1
      else
        echo "  FAIL: fresh doctor after 409 did not pass (HTTP $HTTP_CODE2)" >&2
        FAILURES=$((FAILURES + 1))
        RESULTS="${RESULTS}doctor_exec=409_then_fresh_fail "
      fi
    else
      echo "  FAIL: doctor 409 — no PASS from /api/runs within 90s" >&2
      FAILURES=$((FAILURES + 1))
      RESULTS="${RESULTS}doctor_exec=409_poll_timeout "
    fi
  elif [ -z "$DOCTOR_RESP" ] || [ "$HTTP_CODE" = "000" ]; then
    echo "  FAIL: /api/exec doctor unreachable or timeout" >&2
    [ -n "$DOCTOR_RESP" ] && echo "$DOCTOR_RESP" >"$DOD_ARTIFACT_DIR/exec_doctor.json" || echo '{"ok":false,"error":"unreachable"}' >"$DOD_ARTIFACT_DIR/exec_doctor.json"
    FAILURES=$((FAILURES + 1))
    RESULTS="${RESULTS}doctor_exec=unreachable "
  else
    echo "$DOCTOR_RESP" >"$DOD_ARTIFACT_DIR/exec_doctor.json"
    echo "  FAIL: /api/exec doctor HTTP $HTTP_CODE" >&2
    FAILURES=$((FAILURES + 1))
    RESULTS="${RESULTS}doctor_exec=http_${HTTP_CODE} "
  fi
fi
echo ""

# --- (d) GET /api/artifacts/list, dirs length > 0 ---
echo "==> (d) GET /api/artifacts/list"
ARTIFACTS_RESP="$(curl_with_retries "$BASE_URL/api/artifacts/list")" || true
if [ -z "$ARTIFACTS_RESP" ]; then
  echo "  FAIL: /api/artifacts/list unreachable" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}artifacts_list=unreachable "
else
  echo "$ARTIFACTS_RESP" >"$DOD_ARTIFACT_DIR/artifacts_list.json"
  DIRS_LEN="$(echo "$ARTIFACTS_RESP" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    dirs = d.get('dirs') or []
    print(len(dirs))
except Exception:
    print(0)
" 2>/dev/null)"
  if [ "${DIRS_LEN:-0}" -gt 0 ]; then
    echo "  PASS: /api/artifacts/list dirs length > 0 ($DIRS_LEN)"
  else
    echo "  FAIL: /api/artifacts/list dirs length == 0" >&2
    FAILURES=$((FAILURES + 1))
    RESULTS="${RESULTS}artifacts_list=empty "
  fi
fi
echo ""

# --- (e) Grep for hard-fail strings ---
echo "==> (e) Hard-fail string check (ENOENT, spawn ssh, Host Executor Unreachable)"
HARDFAIL_FOUND=0
for f in "$DOD_ARTIFACT_DIR"/ai_status.json "$DOD_ARTIFACT_DIR"/llm_status.json "$DOD_ARTIFACT_DIR"/project_state.json "$DOD_ARTIFACT_DIR"/exec_doctor.json "$DOD_ARTIFACT_DIR"/artifacts_list.json; do
  [ -f "$f" ] || continue
  if grep -q "ENOENT\|spawn ssh\|Host Executor Unreachable" "$f" 2>/dev/null; then
    echo "  FAIL: hard-fail string found in $(basename "$f")" >&2
    HARDFAIL_FOUND=1
    break
  fi
done
# Also scan recent doctor stdout if present (from exec_doctor.json we have stdout in JSON)
if [ -f "$DOD_ARTIFACT_DIR/exec_doctor.json" ]; then
  STDOUT="$(json_get "$(cat "$DOD_ARTIFACT_DIR/exec_doctor.json")" "stdout")"
  if [ -n "$STDOUT" ] && echo "$STDOUT" | grep -q "ENOENT\|spawn ssh\|Host Executor Unreachable"; then
    echo "  FAIL: hard-fail string in doctor stdout" >&2
    HARDFAIL_FOUND=1
  fi
fi
if [ "$HARDFAIL_FOUND" -eq 1 ]; then
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}hardfail_strings=found "
else
  echo "  PASS: no hard-fail strings in key responses"
fi
echo ""

# --- Write redacted proof artifact ---
python3 -c "
import json
from datetime import datetime, timezone
run_id = '$RUN_ID'
artifact_dir = 'artifacts/dod/$RUN_ID'
failures = int('$FAILURES')
results = '$RESULTS'.strip()
checks = {
  'hostd_health': 'hostd_health' not in results,
  'api_ai_status': 'ai_status' not in results,
  'api_llm_status': 'llm_status' not in results,
  'api_project_state': 'project_state' not in results,
  'doctor_exec': 'doctor_exec' not in results,
  'artifacts_list': 'artifacts_list' not in results,
  'no_hardfail_strings': 'hardfail_strings' not in results,
}
obj = {
  'run_id': run_id,
  'ok': failures == 0,
  'failures': failures,
  'summary': results or 'all pass',
  'checks': checks,
  'artifact_dir': artifact_dir,
  'outputs': {
    'hostd_health': artifact_dir + '/hostd_health.json',
    'ai_status': artifact_dir + '/ai_status.json',
    'llm_status': artifact_dir + '/llm_status.json',
    'project_state': artifact_dir + '/project_state.json',
    'exec_doctor': artifact_dir + '/exec_doctor.json',
    'artifacts_list': artifact_dir + '/artifacts_list.json',
  },
  'timestamps': {'finished': datetime.now(timezone.utc).isoformat()},
}
with open('$DOD_ARTIFACT_DIR/dod_result.json', 'w') as f:
    json.dump(obj, f, indent=2)
"

if [ "$FAILURES" -gt 0 ]; then
  echo "==> DoD FAILED ($FAILURES check(s))" >&2
  exit 1
fi
echo "==> DoD PASSED"
exit 0

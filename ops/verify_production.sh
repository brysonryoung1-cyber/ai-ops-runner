#!/usr/bin/env bash
# verify_production.sh — Post-deploy verification: endpoints ok, doctor/guard PASS, no public ports
#
# Used by deploy_pipeline.sh (and optionally ship_pipeline if combined flow). Exits 0 only if all checks pass.
# Curl: timeouts + retries. Enforces state fields non-null after deploy. Guard strict PASS unless OPENCLAW_VERIFY_GUARD_WARN_OK=1.
# Writes verify_production.json (redacted; no secrets) to SHIP_ARTIFACT_DIR or artifacts/deploy/ or artifacts/ship/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

BASE_URL="${OPENCLAW_VERIFY_BASE_URL:-http://127.0.0.1:8787}"
OUT_DIR="${SHIP_ARTIFACT_DIR:-$ROOT_DIR/artifacts/ship}"
mkdir -p "$OUT_DIR"
RESULT_JSON="$OUT_DIR/verify_production.json"

# Curl: connect timeout 5s, max time 15s, up to 3 retries
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

FAILURES=0
RESULTS=""
STATE_RESP=""

# Use python3 for JSON parsing (jq may not be installed on minimal VPS)
check_ok() { echo "$1" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('ok') is True else 1)" 2>/dev/null; }
json_get() {
  local json="$1" path="$2"
  echo "$json" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for k in [p for p in \"$path\".strip('.').split('.') if p]:
  d=d.get(k) if isinstance(d,dict) else None
  if d is None: break
print(d or '')
" 2>/dev/null || echo ""
}

# --- 1. GET /api/ai-status ---
echo "==> Checking /api/ai-status"
AI_RESP="$(curl_with_retries "$BASE_URL/api/ai-status")" || true
if [ -z "$AI_RESP" ]; then
  echo "  FAIL: /api/ai-status unreachable or non-ok" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}ai_status=unreachable "
elif ! check_ok "$AI_RESP"; then
  echo "  FAIL: /api/ai-status ok != true" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}ai_status=not_ok "
else
  echo "  PASS: /api/ai-status ok:true"
fi

# --- 2. GET /api/llm/status ---
echo "==> Checking /api/llm/status"
LLM_RESP="$(curl_with_retries "$BASE_URL/api/llm/status")" || true
if [ -z "$LLM_RESP" ]; then
  echo "  FAIL: /api/llm/status unreachable or non-ok" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}llm_status=unreachable "
elif ! check_ok "$LLM_RESP"; then
  echo "  FAIL: /api/llm/status ok != true" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}llm_status=not_ok "
else
  echo "  PASS: /api/llm/status ok:true"
fi

# --- 3. GET /api/project/state (enforce non-null after deploy) ---
echo "==> Checking /api/project/state"
STATE_RESP="$(curl_with_retries "$BASE_URL/api/project/state")" || true
if [ -z "$STATE_RESP" ]; then
  echo "  FAIL: /api/project/state unreachable or non-ok" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}project_state=unreachable "
elif ! check_ok "$STATE_RESP"; then
  echo "  FAIL: /api/project/state ok != true" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}project_state=not_ok "
else
  LAST_DEPLOY="$(json_get "$STATE_RESP" ".state.last_deploy_timestamp")"
  LAST_DOCTOR="$(json_get "$STATE_RESP" ".state.last_doctor_result")"
  LAST_GUARD="$(json_get "$STATE_RESP" ".state.last_guard_result")"
  LAST_HEAD="$(json_get "$STATE_RESP" ".state.last_verified_vps_head")"
  if [ -n "$LAST_DEPLOY" ]; then
    echo "  PASS: /api/project/state ok:true (last_deploy: $LAST_DEPLOY)"
  else
    echo "  FAIL: last_deploy_timestamp missing after deploy (state must be updated)" >&2
    FAILURES=$((FAILURES + 1))
    RESULTS="${RESULTS}project_state=missing_deploy_ts "
  fi
  # After deploy we expect doctor and guard to be populated (update_project_state runs after verify; so first run may have null guard)
  if [ -z "$LAST_HEAD" ]; then
    echo "  WARN: last_verified_vps_head missing" >&2
  fi
fi

# --- 4. Doctor: run openclaw_doctor.sh, require PASS ---
echo "==> Checking doctor (openclaw_doctor.sh)"
DOCTOR_OUT=""
DOCTOR_RC=0
"$SCRIPT_DIR/openclaw_doctor.sh" >"$OUT_DIR/doctor_verify.log" 2>&1 || DOCTOR_RC=$?
DOCTOR_OUT="$(cat "$OUT_DIR/doctor_verify.log")"
if [ "$DOCTOR_RC" -ne 0 ]; then
  echo "  FAIL: doctor exited $DOCTOR_RC" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}doctor=exit_nonzero "
elif echo "$DOCTOR_OUT" | grep -q "^  FAIL:"; then
  echo "  FAIL: doctor reported failures" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}doctor=checks_failed "
else
  echo "  PASS: doctor all checks passed"
fi

# --- 5. Guard: strict PASS by default; warn only if OPENCLAW_VERIFY_GUARD_WARN_OK=1 ---
echo "==> Checking guard"
GUARD_PASS=0
if [ -n "$STATE_RESP" ] && check_ok "$STATE_RESP"; then
  GUARD_RES="$(json_get "$STATE_RESP" ".state.last_guard_result")"
  if [ "$GUARD_RES" = "PASS" ]; then
    GUARD_PASS=1
  fi
fi
if [ "$GUARD_PASS" -eq 0 ] && [ -f /var/log/openclaw_guard.log ]; then
  if tail -50 /var/log/openclaw_guard.log 2>/dev/null | grep -q "RESULT: PASS"; then
    GUARD_PASS=1
  fi
fi
if [ "$GUARD_PASS" -eq 0 ]; then
  if [ "${OPENCLAW_VERIFY_GUARD_WARN_OK:-0}" = "1" ]; then
    echo "  WARN: guard last result not PASS (OPENCLAW_VERIFY_GUARD_WARN_OK=1)" >&2
    RESULTS="${RESULTS}guard=warn "
  else
    echo "  FAIL: guard must be PASS (set OPENCLAW_VERIFY_GUARD_WARN_OK=1 to allow warn)" >&2
    FAILURES=$((FAILURES + 1))
    RESULTS="${RESULTS}guard=fail "
  fi
else
  echo "  PASS: guard last result PASS"
fi

# --- 6. No public ports (doctor output already checked; re-assert) ---
if echo "$DOCTOR_OUT" | grep -q "UNEXPECTED PUBLIC PORT BINDINGS\|Public Port Audit.*FAIL"; then
  echo "  FAIL: public port bindings detected" >&2
  FAILURES=$((FAILURES + 1))
  RESULTS="${RESULTS}public_ports=fail "
else
  echo "  PASS: no public port violations"
fi

# --- Write machine-readable result (no secrets) ---
export VERIFY_OUT_DIR="$OUT_DIR" VERIFY_FAILURES="$FAILURES" VERIFY_RESULTS="$RESULTS"
python3 -c "
import json, os
out_dir = os.environ.get('VERIFY_OUT_DIR', '')
failures = int(os.environ.get('VERIFY_FAILURES', '0'))
results = (os.environ.get('VERIFY_RESULTS') or '').strip()
obj = {
  'ok': failures == 0,
  'failures': failures,
  'checks': {
    'api_ai_status': 'ai_status' not in results,
    'api_llm_status': 'llm_status' not in results,
    'api_project_state': 'project_state' not in results,
    'doctor': 'doctor' not in results,
    'guard': 'guard' not in results,
    'no_public_ports': 'public_ports' not in results,
  },
  'summary': results or 'all pass',
}
with open(out_dir + '/verify_production.json', 'w') as f:
    json.dump(obj, f, indent=2)
"

if [ "$FAILURES" -gt 0 ]; then
  echo ""
  echo "==> Verification FAILED ($FAILURES check(s))" >&2
  exit 1
fi
echo ""
echo "==> Verification PASSED"
exit 0

#!/usr/bin/env bash
# system.canary — Continuous self-check: reconcile + novnc audit + ask smoke + version drift.
#
# Runs: state_pack + invariants, novnc_connectivity_audit, /api/ask "Is noVNC reachable?",
#       /api/ui/version drift check.
# Writes: artifacts/system/canary/<run_id>/PROOF.md
# Fail-closed: if any check fails, create incident, attempt one reconcile remediation;
# if still failing, stop and mark degraded.
# No LLM required.
set -eu
# Avoid SIGPIPE (141) from pipes when reader closes early; use file redirects instead

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ARTIFACTS="${OPENCLAW_ARTIFACTS_ROOT:-$ROOT_DIR/artifacts}"
RUN_ID="canary_$(date -u +%Y%m%dT%H%M%SZ)_$(od -A n -t x4 -N 2 /dev/urandom 2>/dev/null | tr -d ' ' || echo $$)"
CANARY_DIR="$ARTIFACTS/system/canary/$RUN_ID"
INCIDENTS_DIR="$ARTIFACTS/incidents"
CONSOLE_BASE="${OPENCLAW_VERIFY_BASE_URL:-http://127.0.0.1:8787}"
TS_HOSTNAME="${OPENCLAW_TS_HOSTNAME:-aiops-1.tailc75c62.ts.net}"
mkdir -p "$CANARY_DIR" "$INCIDENTS_DIR"

# Concurrency lock (flock when available, else mkdir)
LOCK_DIR="${OPENCLAW_CANARY_LOCK_DIR:-$ROOT_DIR/.locks}"
mkdir -p "$LOCK_DIR"
LOCK_FILE="$LOCK_DIR/canary.lock"
LOCK_ACQUIRED=0
if command -v flock >/dev/null 2>&1; then
  exec 202>"$LOCK_FILE"
  if flock -n 202 2>/dev/null; then LOCK_ACQUIRED=1; fi
else
  LOCK_MKDIR="${LOCK_FILE}.d"
  if mkdir "$LOCK_MKDIR" 2>/dev/null; then
    LOCK_ACQUIRED=1
    trap 'rmdir "$LOCK_MKDIR" 2>/dev/null' EXIT
  fi
fi
if [ "$LOCK_ACQUIRED" -eq 0 ]; then
  echo '{"status":"SKIP","reason":"lock_contention","run_id":"'"$RUN_ID"'"}' > "$CANARY_DIR/result.json"
  exit 2
fi

write_incident() {
  local inc_id="$1"
  local status="$2"
  local summary="$3"
  local inc_dir="$INCIDENTS_DIR/$inc_id"
  mkdir -p "$inc_dir"
  [ -d "$CANARY_DIR" ] && cp -r "$CANARY_DIR" "$inc_dir/canary_run" 2>/dev/null || true
  cat > "$inc_dir/SUMMARY.md" << EOF
# Incident: $inc_id

**Status:** $status
**Timestamp:** $(date -u +%Y-%m-%dT%H:%M:%SZ)

## Summary
$summary

## Canary run
$CANARY_DIR
EOF
}

FAILED_INVARIANT=""
REMEDIATE=0
CORE_FAILED_CHECKS=""
OPTIONAL_FAILED_CHECKS=""

CHECK_RECONCILE_STATUS="PASS"
CHECK_RECONCILE_REASON=""
CHECK_NOVNC_STATUS="PASS"
CHECK_NOVNC_REASON=""
CHECK_ASK_STATUS="PASS"
CHECK_ASK_REASON=""
CHECK_VERSION_STATUS="PASS"
CHECK_VERSION_REASON=""

append_csv() {
  local var_name="$1"
  local value="$2"
  local current="${!var_name:-}"
  if [ -z "$value" ]; then
    return 0
  fi
  if [ -z "$current" ]; then
    printf -v "$var_name" "%s" "$value"
  else
    printf -v "$var_name" "%s,%s" "$current" "$value"
  fi
}

mark_core_fail() {
  local code="$1"
  append_csv CORE_FAILED_CHECKS "$code"
  [ -z "$FAILED_INVARIANT" ] && FAILED_INVARIANT="$code"
  REMEDIATE=1
}

mark_optional_fail() {
  local code="$1"
  append_csv OPTIONAL_FAILED_CHECKS "$code"
}

write_result_json() {
  local status="$1"
  local remediation_suppressed="${2:-false}"
  local remediation_reason="${3:-}"
  local incident_id="${4:-}"
  local fixpack_path="${5:-}"
  CANARY_STATUS="$status" \
  RUN_ID="$RUN_ID" \
  CANARY_DIR="$CANARY_DIR" \
  CORE_FAILED_CHECKS="$CORE_FAILED_CHECKS" \
  OPTIONAL_FAILED_CHECKS="$OPTIONAL_FAILED_CHECKS" \
  REMEDIATION_SUPPRESSED="$remediation_suppressed" \
  REMEDIATION_REASON="$remediation_reason" \
  INCIDENT_ID="$incident_id" \
  FIXPACK_PATH="$fixpack_path" \
  CHECK_RECONCILE_STATUS="$CHECK_RECONCILE_STATUS" \
  CHECK_RECONCILE_REASON="$CHECK_RECONCILE_REASON" \
  CHECK_NOVNC_STATUS="$CHECK_NOVNC_STATUS" \
  CHECK_NOVNC_REASON="$CHECK_NOVNC_REASON" \
  CHECK_ASK_STATUS="$CHECK_ASK_STATUS" \
  CHECK_ASK_REASON="$CHECK_ASK_REASON" \
  CHECK_VERSION_STATUS="$CHECK_VERSION_STATUS" \
  CHECK_VERSION_REASON="$CHECK_VERSION_REASON" \
  python3 - << 'PY' > "$CANARY_DIR/result.json"
import json
import os

def csv_items(name: str) -> list[str]:
    return [item for item in os.environ.get(name, "").split(",") if item]

def nullable(text: str) -> str | None:
    value = text.strip()
    return value or None

core_failed = csv_items("CORE_FAILED_CHECKS")
optional_failed = csv_items("OPTIONAL_FAILED_CHECKS")
core_status = "PASS" if not core_failed else "FAIL"
optional_status = "PASS" if not optional_failed else "WARN"

status = os.environ.get("CANARY_STATUS", "PASS")
if status == "PASS" and core_status != "PASS":
    status = "DEGRADED"

payload = {
    "status": status,
    "run_id": os.environ["RUN_ID"],
    "proof": f"{os.environ['CANARY_DIR']}/PROOF.md",
    "core_status": core_status,
    "optional_status": optional_status,
    "core_failed_checks": core_failed,
    "optional_failed_checks": optional_failed,
    "failed_invariant": core_failed[0] if core_failed else None,
    "checks": {
        "reconcile_core": {
            "status": os.environ.get("CHECK_RECONCILE_STATUS", "PASS"),
            "severity": "CORE",
            "reason": nullable(os.environ.get("CHECK_RECONCILE_REASON", "")),
        },
        "novnc_connectivity": {
            "status": os.environ.get("CHECK_NOVNC_STATUS", "PASS"),
            "severity": "CORE",
            "reason": nullable(os.environ.get("CHECK_NOVNC_REASON", "")),
        },
        "ask_unreachable": {
            "status": os.environ.get("CHECK_ASK_STATUS", "PASS"),
            "severity": "OPTIONAL",
            "reason": nullable(os.environ.get("CHECK_ASK_REASON", "")),
        },
        "version_drift": {
            "status": os.environ.get("CHECK_VERSION_STATUS", "PASS"),
            "severity": "CORE",
            "reason": nullable(os.environ.get("CHECK_VERSION_REASON", "")),
        },
    },
}

if os.environ.get("REMEDIATION_SUPPRESSED") == "true":
    payload["remediation_suppressed"] = True
if os.environ.get("REMEDIATION_REASON"):
    payload["reason"] = os.environ["REMEDIATION_REASON"]
if os.environ.get("INCIDENT_ID"):
    payload["incident_id"] = os.environ["INCIDENT_ID"]
if os.environ.get("FIXPACK_PATH"):
    payload["fixpack_path"] = os.environ["FIXPACK_PATH"]

print(json.dumps(payload))
PY
}

echo "=== system.canary ($RUN_ID) ==="
echo ""

# --- 1. Reconcile core (state_pack + invariants) ---
echo "==> 1. Reconcile core (state_pack + invariants)"
SP_RUN_ID="canary_${RUN_ID}_sp"
OPENCLAW_RUN_ID="$SP_RUN_ID" "$ROOT_DIR/ops/scripts/state_pack.sh" 2>/dev/null > "$CANARY_DIR/state_pack_raw.json" || true
tail -1 "$CANARY_DIR/state_pack_raw.json" 2>/dev/null > "$CANARY_DIR/state_pack_result.json" || true
SP_DIR=$(ls -1dt "$ARTIFACTS/system/state_pack"/*/ 2>/dev/null | head -1)
RECONCILE_PROOF=""
if [ -n "$SP_DIR" ]; then
  OPENCLAW_STATE_PACK_RUN_ID=$(basename "$SP_DIR") OPENCLAW_INVARIANTS_OUTPUT="$CANARY_DIR/invariants.json" \
    python3 "$ROOT_DIR/ops/scripts/invariants_eval.py" > "$CANARY_DIR/invariants_stdout.json" 2>/dev/null || true
  ALL_PASS=$(python3 -c "import json; d=json.load(open('$CANARY_DIR/invariants.json')); print(d.get('all_pass', False))" 2>/dev/null) || ALL_PASS="False"
  RECONCILE_PROOF="artifacts/system/state_pack/$(basename "$SP_DIR")"
  if [ "$ALL_PASS" != "True" ]; then
    FAILED_INV=$(python3 -c "
import json
try:
    d=json.load(open('$CANARY_DIR/invariants.json'))
    failing=[i['id'] for i in d.get('invariants',[]) if not i.get('pass')]
    print(','.join(failing) if failing else 'unknown')
except: print('unknown')
" 2>/dev/null)
    CHECK_RECONCILE_STATUS="FAIL"
    CHECK_RECONCILE_REASON="$FAILED_INV"
    echo "  Reconcile: FAIL ($FAILED_INV)"
    mark_core_fail "$FAILED_INV"
  else
    echo "  Reconcile: PASS"
  fi
else
  echo "  Reconcile: FAIL (state_pack failed)"
  CHECK_RECONCILE_STATUS="FAIL"
  CHECK_RECONCILE_REASON="state_pack_failed"
  mark_core_fail "state_pack_failed"
fi
echo ""

# --- 2. noVNC connectivity audit (STRICT: fail if noVNC stack not running) ---
echo "==> 2. noVNC connectivity audit"
NOVNC_RUN_ID="canary_${RUN_ID}_novnc"
NOVNC_STACK_AVAILABLE=false
[ -n "$SP_DIR" ] && [ -f "$SP_DIR/ports.txt" ] && grep -qE ":6080|6080" "$SP_DIR/ports.txt" 2>/dev/null && NOVNC_STACK_AVAILABLE=true
if [ "$NOVNC_STACK_AVAILABLE" != true ]; then
  echo "  noVNC audit: FAIL (6080 not listening)"
  echo '{"all_ok":false,"error":"noVNC stack down (6080 not listening)"}' > "$CANARY_DIR/novnc_audit.json" 2>/dev/null || true
  CHECK_NOVNC_STATUS="FAIL"
  CHECK_NOVNC_REASON="novnc_stack_down"
  mark_core_fail "novnc_stack_down"
else
  python3 "$ROOT_DIR/ops/scripts/novnc_connectivity_audit.py" --run-id "$NOVNC_RUN_ID" --host "$TS_HOSTNAME" > "$CANARY_DIR/novnc_audit.json" 2>/dev/null || true
  NOVNC_OK=$(python3 -c "import json; d=json.load(open('$CANARY_DIR/novnc_audit.json')); print(d.get('all_ok', False))" 2>/dev/null) || NOVNC_OK="False"
  if [ "$NOVNC_OK" != "True" ]; then
    echo "  noVNC audit: FAIL"
    # Collect hop-by-hop diagnostic on failure
    if [ -f "$ROOT_DIR/ops/scripts/ws_upgrade_hop_probe.sh" ]; then
      OPENCLAW_HOP_PROBE_RUN_ID="${RUN_ID}_canary_hop" \
        bash "$ROOT_DIR/ops/scripts/ws_upgrade_hop_probe.sh" > "$CANARY_DIR/hop_probe.log" 2>&1 || true
    fi
    CHECK_NOVNC_STATUS="FAIL"
    CHECK_NOVNC_REASON="novnc_audit_failed"
    mark_core_fail "novnc_audit_failed"
  else
    echo "  noVNC audit: PASS"
  fi
fi
WS_PROOF="artifacts/novnc_debug/ws_probe/$NOVNC_RUN_ID"
echo ""

# --- 3. /api/ask smoke (deterministic; no Kajabi/auth) ---
echo "==> 3. /api/ask smoke"
ASK_QUESTION='{"question":"Is noVNC reachable?"}'
if curl -sf --connect-timeout 5 --max-time 15 -X POST "$CONSOLE_BASE/api/ask" \
  -H "Content-Type: application/json" \
  -d "$ASK_QUESTION" 2>/dev/null > "$CANARY_DIR/ask_response.json"; then
  ASK_OK=$(python3 -c "
import json
try:
    d=json.load(open('$CANARY_DIR/ask_response.json'))
    ok = d.get('ok') and (len(d.get('citations',[])) > 0 or 'deploy' in str(d).lower() or 'sha' in str(d).lower())
    print(ok)
except: print(False)
" 2>/dev/null)
  if [ "$ASK_OK" = "True" ]; then
    echo "  Ask smoke: PASS"
  else
    echo "  Ask smoke: WARN (no citations or ok=false)"
    CHECK_ASK_STATUS="WARN"
    CHECK_ASK_REASON="ask_smoke_failed"
    mark_optional_fail "ask_smoke_failed"
  fi
else
  echo "  Ask smoke: WARN (curl failed)"
  CHECK_ASK_STATUS="WARN"
  CHECK_ASK_REASON="ask_unreachable"
  mark_optional_fail "ask_unreachable"
fi
echo ""

# --- 4. /api/ui/version drift check (fail-closed: unknown or true = FAIL) ---
echo "==> 4. Version drift check"
if curl -sf --connect-timeout 3 --max-time 5 "$CONSOLE_BASE/api/ui/version" 2>/dev/null > "$CANARY_DIR/version.json"; then
  DRIFT_STATUS=$(python3 -c "import json; d=json.load(open('$CANARY_DIR/version.json')); print(d.get('drift_status','unknown'))" 2>/dev/null) || DRIFT_STATUS="unknown"
  DRIFT=$(python3 -c "import json; d=json.load(open('$CANARY_DIR/version.json')); v=d.get('drift'); print('true' if v is True else 'false')" 2>/dev/null) || DRIFT="true"
  if [ "$DRIFT_STATUS" = "unknown" ] || [ "$DRIFT" = "true" ]; then
    echo "  Version drift: FAIL (drift_status=$DRIFT_STATUS drift=$DRIFT)"
    CHECK_VERSION_STATUS="FAIL"
    CHECK_VERSION_REASON="version_drift"
    mark_core_fail "version_drift"
  else
    echo "  Version drift: PASS"
  fi
else
  echo "  Version drift: FAIL (unreachable)"
  CHECK_VERSION_STATUS="FAIL"
  CHECK_VERSION_REASON="version_unreachable"
  mark_core_fail "version_unreachable"
fi
echo ""

# --- Remediation: one reconcile cycle if any failed ---
if [ "$REMEDIATE" -eq 1 ]; then
  # Suppress disruptive remediation during active login window
  GATE_CHECK="$ROOT_DIR/ops/scripts/csr_human_gate_check.sh"
  if [ -x "$GATE_CHECK" ] && "$GATE_CHECK" soma_kajabi >/dev/null 2>&1; then
    GATE_INFO=$("$GATE_CHECK" soma_kajabi 2>/dev/null || true)
    echo "  Login window active — remediation suppressed"
    mkdir -p "$CANARY_DIR"
    cat > "$CANARY_DIR/gate_suppression.json" << GEOF
{"remediation_suppressed": true, "reason": "remediation suppressed due to active login window", "gate_info": $GATE_INFO, "failed_invariant": "$FAILED_INVARIANT", "timestamp_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
GEOF
    write_result_json "DEGRADED" "true" "active login window" "" ""
    cat > "$CANARY_DIR/PROOF.md" << EOF
# Canary DEGRADED (remediation suppressed)

**Run ID:** $RUN_ID
**Timestamp:** $(date -u +%Y-%m-%dT%H:%M:%SZ)
**Failed invariant:** $FAILED_INVARIANT
**Suppression:** remediation suppressed due to active login window

## Artifacts
- invariants: $CANARY_DIR/invariants.json
- gate_suppression: $CANARY_DIR/gate_suppression.json
EOF
    cat "$CANARY_DIR/result.json"
    exit 1
  fi

  echo "==> Remediation: one reconcile cycle"
  PLAYBOOK="recover_hq_routing"
  NOVNC_FAILURE=0
  if echo "$FAILED_INVARIANT" | grep -q "novnc\|ws_probe"; then
    PLAYBOOK="recover_novnc_ws"
    NOVNC_FAILURE=1
  elif echo "$FAILED_INVARIANT" | grep -q "frontdoor\|serve"; then
    PLAYBOOK="reconcile_frontdoor_serve"
  fi

  # For noVNC failures: run autorecover first (covers shm, routing, doctor)
  if [ "$NOVNC_FAILURE" -eq 1 ] && [ -f "$ROOT_DIR/ops/scripts/novnc_autorecover.py" ]; then
    echo "  Running novnc_autorecover..."
    OPENCLAW_RUN_ID="${RUN_ID}_autorecover" python3 "$ROOT_DIR/ops/scripts/novnc_autorecover.py" > "$CANARY_DIR/autorecover.log" 2>&1 && {
      echo "  novnc_autorecover: PASS"
    } || {
      echo "  novnc_autorecover: FAIL (falling through to playbook)"
    }
  fi

  case "$PLAYBOOK" in
    reconcile_frontdoor_serve) bash "$ROOT_DIR/ops/playbooks/reconcile_frontdoor_serve.sh" > "$CANARY_DIR/playbook.log" 2>&1 || true; tail -3 "$CANARY_DIR/playbook.log" 2>/dev/null || true ;;
    recover_novnc_ws)         bash "$ROOT_DIR/ops/playbooks/recover_novnc_ws.sh" > "$CANARY_DIR/playbook.log" 2>&1 || true; tail -3 "$CANARY_DIR/playbook.log" 2>/dev/null || true ;;
    *)                       bash "$ROOT_DIR/ops/playbooks/recover_hq_routing.sh" > "$CANARY_DIR/playbook.log" 2>&1 || true; tail -3 "$CANARY_DIR/playbook.log" 2>/dev/null || true ;;
  esac
  sleep 15
  echo "  Re-running canary checks..."
  # Re-run critical checks (remediation may have started noVNC)
  python3 "$ROOT_DIR/ops/scripts/novnc_connectivity_audit.py" --run-id "${NOVNC_RUN_ID}_retry" --host "$TS_HOSTNAME" > "$CANARY_DIR/novnc_audit_retry.json" 2>/dev/null || true
  NOVNC_RETRY=$(python3 -c "import json; d=json.load(open('$CANARY_DIR/novnc_audit_retry.json')); print(d.get('all_ok', False))" 2>/dev/null) || NOVNC_RETRY="False"
  curl -sf --connect-timeout 3 --max-time 5 "$CONSOLE_BASE/api/ui/version" 2>/dev/null > "$CANARY_DIR/version_retry.json" || true
  DRIFT_STATUS_RETRY=$(python3 -c "import json; d=json.load(open('$CANARY_DIR/version_retry.json')); print(d.get('drift_status','unknown'))" 2>/dev/null) || DRIFT_STATUS_RETRY="unknown"
  DRIFT_RETRY=$(python3 -c "import json; d=json.load(open('$CANARY_DIR/version_retry.json')); v=d.get('drift'); print('true' if v is True else 'false')" 2>/dev/null) || DRIFT_RETRY="true"
  if [ "$NOVNC_RETRY" = "True" ] && [ "$DRIFT_STATUS_RETRY" = "ok" ] && [ "$DRIFT_RETRY" != "true" ]; then
    echo "  Remediation: PASS (checks recovered)"
    REMEDIATE=0
    CORE_FAILED_CHECKS=""
    FAILED_INVARIANT=""
    if [ "$CHECK_NOVNC_STATUS" = "FAIL" ]; then
      CHECK_NOVNC_STATUS="PASS"
      CHECK_NOVNC_REASON="recovered_after_remediation"
    fi
    if [ "$CHECK_VERSION_STATUS" = "FAIL" ]; then
      CHECK_VERSION_STATUS="PASS"
      CHECK_VERSION_REASON="recovered_after_remediation"
    fi
  else
    echo "  Remediation: FAIL (still degraded after autorecover + playbook)"
    INC_ID="incident_canary_${RUN_ID}"
    write_incident "$INC_ID" "DEGRADED" "Canary failed: $FAILED_INVARIANT. Proof: $CANARY_DIR"
    # Emit fixpack for noVNC failures so CSR can pick up structured triage
    if [ "$NOVNC_FAILURE" -eq 1 ] && [ -f "$ROOT_DIR/ops/scripts/novnc_fixpack_emit.sh" ]; then
      FIXPACK_DIR="$CANARY_DIR/fixpack"
      mkdir -p "$FIXPACK_DIR"
      bash "$ROOT_DIR/ops/scripts/novnc_fixpack_emit.sh" "$FIXPACK_DIR" "novnc_audit_failed" "canary_novnc_check" "run novnc_autorecover or escalate" \
        "novnc_audit:$CANARY_DIR/novnc_audit.json" "novnc_audit_retry:$CANARY_DIR/novnc_audit_retry.json" 2>/dev/null || true
    fi
    write_result_json "DEGRADED" "false" "" "$INC_ID" "${FIXPACK_DIR:-}"
    # Notify: canary degraded (N consecutive failures tracked via incident ledger)
    CONSECUTIVE=$(ls -1dt "$ARTIFACTS/system/canary"/*/result.json 2>/dev/null | head -5 | while read f; do
      grep -q '"status":"DEGRADED"' "$f" 2>/dev/null && echo 1; done | wc -l)
    if [ "${CONSECUTIVE:-0}" -ge 2 ]; then
      "$ROOT_DIR/ops/scripts/notify_banner.sh" CANARY_DEGRADED "{\"failed_invariant\":\"$FAILED_INVARIANT\",\"failed_checks\":[\"$FAILED_INVARIANT\"],\"proof_paths\":[\"$CANARY_DIR/PROOF.md\"],\"severity\":\"CORE\"}" 2>/dev/null || true
    fi
    cat > "$CANARY_DIR/PROOF.md" << EOF
# Canary DEGRADED

**Run ID:** $RUN_ID
**Timestamp:** $(date -u +%Y-%m-%dT%H:%M:%SZ)
**Failed invariant:** $FAILED_INVARIANT

## Artifacts
- invariants: $CANARY_DIR/invariants.json
- novnc_audit: $CANARY_DIR/novnc_audit.json
- ask_response: $CANARY_DIR/ask_response.json
- version: $CANARY_DIR/version.json
- incident: $INCIDENTS_DIR/$INC_ID
EOF
    cat "$CANARY_DIR/result.json"
    exit 1
  fi
fi

# --- PASS: Write PROOF.md ---
cat > "$CANARY_DIR/PROOF.md" << EOF
# Canary PASS

**Run ID:** $RUN_ID
**Timestamp:** $(date -u +%Y-%m-%dT%H:%M:%SZ)

## Checks
- Reconcile (state_pack + invariants): PASS
- noVNC connectivity audit: PASS
- /api/ask smoke: ${CHECK_ASK_STATUS}
- /api/ui/version drift: PASS

## Artifacts
- Reconcile: $RECONCILE_PROOF
- noVNC ws_probe: $WS_PROOF
- Ask response: $CANARY_DIR/ask_response.json
- Version: $CANARY_DIR/version.json
EOF

if [ -n "$OPTIONAL_FAILED_CHECKS" ]; then
  cat >> "$CANARY_DIR/PROOF.md" << EOF

## Optional warnings
- $OPTIONAL_FAILED_CHECKS
EOF
fi

write_result_json "PASS" "false" "" "" ""
echo "=== canary COMPLETE ==="
echo "  Proof: $CANARY_DIR/PROOF.md"
cat "$CANARY_DIR/result.json"
exit 0

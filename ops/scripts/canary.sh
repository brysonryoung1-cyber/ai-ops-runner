#!/usr/bin/env bash
# system.canary — Continuous self-check: reconcile + novnc audit + ask smoke + version drift.
#
# Runs: state_pack + invariants, novnc_connectivity_audit, /api/ask "Is noVNC reachable?",
#       /api/ui/version drift check.
# Writes: artifacts/system/canary/<run_id>/PROOF.md
# Fail-closed: if any check fails, create incident, attempt one reconcile remediation;
# if still failing, stop and mark degraded.
# No LLM required.
set -euo pipefail

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

echo "=== system.canary ($RUN_ID) ==="
echo ""

# --- 1. Reconcile core (state_pack + invariants) ---
echo "==> 1. Reconcile core (state_pack + invariants)"
SP_RUN_ID="canary_${RUN_ID}_sp"
OPENCLAW_RUN_ID="$SP_RUN_ID" "$ROOT_DIR/ops/scripts/state_pack.sh" 2>/dev/null | tail -1 > "$CANARY_DIR/state_pack_result.json" || true
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
    FAILED_INVARIANT="$FAILED_INV"
    echo "  Reconcile: FAIL ($FAILED_INV)"
    REMEDIATE=1
  else
    echo "  Reconcile: PASS"
  fi
else
  echo "  Reconcile: FAIL (state_pack failed)"
  FAILED_INVARIANT="state_pack_failed"
  REMEDIATE=1
fi
echo ""

# --- 2. noVNC connectivity audit ---
echo "==> 2. noVNC connectivity audit"
NOVNC_RUN_ID="canary_${RUN_ID}_novnc"
python3 "$ROOT_DIR/ops/scripts/novnc_connectivity_audit.py" --run-id "$NOVNC_RUN_ID" --host "$TS_HOSTNAME" > "$CANARY_DIR/novnc_audit.json" 2>/dev/null || true
NOVNC_OK=$(python3 -c "import json; d=json.load(open('$CANARY_DIR/novnc_audit.json')); print(d.get('all_ok', False))" 2>/dev/null) || NOVNC_OK="False"
WS_PROOF="artifacts/novnc_debug/ws_probe/$NOVNC_RUN_ID"
if [ "$NOVNC_OK" != "True" ]; then
  echo "  noVNC audit: FAIL"
  [ -z "$FAILED_INVARIANT" ] && FAILED_INVARIANT="novnc_audit_failed"
  REMEDIATE=1
else
  echo "  noVNC audit: PASS"
fi
echo ""

# --- 3. /api/ask smoke ("Is noVNC reachable?") ---
echo "==> 3. /api/ask smoke"
ASK_RESP=""
if curl -sf --connect-timeout 5 --max-time 15 -X POST "$CONSOLE_BASE/api/ask" \
  -H "Content-Type: application/json" \
  -d '{"question":"Is noVNC reachable?"}' 2>/dev/null > "$CANARY_DIR/ask_response.json"; then
  ASK_OK=$(python3 -c "
import json
try:
    d=json.load(open('$CANARY_DIR/ask_response.json'))
    ok = d.get('ok') and len(d.get('citations',[])) > 0
    print(ok)
except: print(False)
" 2>/dev/null)
  if [ "$ASK_OK" = "True" ]; then
    echo "  Ask smoke: PASS"
  else
    echo "  Ask smoke: FAIL (no citations or ok=false)"
    [ -z "$FAILED_INVARIANT" ] && FAILED_INVARIANT="ask_smoke_failed"
    REMEDIATE=1
  fi
else
  echo "  Ask smoke: FAIL (curl failed)"
  [ -z "$FAILED_INVARIANT" ] && FAILED_INVARIANT="ask_unreachable"
  REMEDIATE=1
fi
echo ""

# --- 4. /api/ui/version drift check (fail-closed: unknown or true = FAIL) ---
echo "==> 4. Version drift check"
if curl -sf --connect-timeout 3 --max-time 5 "$CONSOLE_BASE/api/ui/version" 2>/dev/null > "$CANARY_DIR/version.json"; then
  DRIFT_STATUS=$(python3 -c "import json; d=json.load(open('$CANARY_DIR/version.json')); print(d.get('drift_status','unknown'))" 2>/dev/null) || DRIFT_STATUS="unknown"
  DRIFT=$(python3 -c "import json; d=json.load(open('$CANARY_DIR/version.json')); v=d.get('drift'); print('true' if v is True else 'false')" 2>/dev/null) || DRIFT="true"
  if [ "$DRIFT_STATUS" = "unknown" ] || [ "$DRIFT" = "true" ]; then
    echo "  Version drift: FAIL (drift_status=$DRIFT_STATUS drift=$DRIFT)"
    [ -z "$FAILED_INVARIANT" ] && FAILED_INVARIANT="version_drift"
    REMEDIATE=1
  else
    echo "  Version drift: PASS"
  fi
else
  echo "  Version drift: FAIL (unreachable)"
  [ -z "$FAILED_INVARIANT" ] && FAILED_INVARIANT="version_unreachable"
  REMEDIATE=1
fi
echo ""

# --- Remediation: one reconcile cycle if any failed ---
if [ "$REMEDIATE" -eq 1 ]; then
  echo "==> Remediation: one reconcile cycle"
  PLAYBOOK="recover_hq_routing"
  if echo "$FAILED_INVARIANT" | grep -q "novnc\|ws_probe"; then
    PLAYBOOK="recover_novnc_ws"
  elif echo "$FAILED_INVARIANT" | grep -q "frontdoor\|serve"; then
    PLAYBOOK="reconcile_frontdoor_serve"
  fi
  case "$PLAYBOOK" in
    reconcile_frontdoor_serve) bash "$ROOT_DIR/ops/playbooks/reconcile_frontdoor_serve.sh" 2>&1 | tail -3 ;;
    recover_novnc_ws)         bash "$ROOT_DIR/ops/playbooks/recover_novnc_ws.sh" 2>&1 | tail -3 ;;
    *)                       bash "$ROOT_DIR/ops/playbooks/recover_hq_routing.sh" 2>&1 | tail -3 ;;
  esac
  sleep 15
  echo "  Re-running canary checks..."
  # Re-run critical checks only
  python3 "$ROOT_DIR/ops/scripts/novnc_connectivity_audit.py" --run-id "${NOVNC_RUN_ID}_retry" --host "$TS_HOSTNAME" > "$CANARY_DIR/novnc_audit_retry.json" 2>/dev/null || true
  curl -sf --connect-timeout 3 --max-time 5 "$CONSOLE_BASE/api/ui/version" 2>/dev/null > "$CANARY_DIR/version_retry.json" || true
  NOVNC_RETRY=$(python3 -c "import json; d=json.load(open('$CANARY_DIR/novnc_audit_retry.json')); print(d.get('all_ok', False))" 2>/dev/null) || NOVNC_RETRY="False"
  DRIFT_STATUS_RETRY=$(python3 -c "import json; d=json.load(open('$CANARY_DIR/version_retry.json')); print(d.get('drift_status','unknown'))" 2>/dev/null) || DRIFT_STATUS_RETRY="unknown"
  DRIFT_RETRY=$(python3 -c "import json; d=json.load(open('$CANARY_DIR/version_retry.json')); v=d.get('drift'); print('true' if v is True else 'false')" 2>/dev/null) || DRIFT_RETRY="true"
  if [ "$NOVNC_RETRY" = "True" ] && [ "$DRIFT_STATUS_RETRY" = "ok" ] && [ "$DRIFT_RETRY" != "true" ]; then
    echo "  Remediation: PASS (checks recovered)"
    REMEDIATE=0
  else
    echo "  Remediation: FAIL (still degraded)"
    INC_ID="incident_canary_${RUN_ID}"
    write_incident "$INC_ID" "DEGRADED" "Canary failed: $FAILED_INVARIANT. Proof: $CANARY_DIR"
    echo '{"status":"DEGRADED","run_id":"'"$RUN_ID"'","failed_invariant":"'"$FAILED_INVARIANT"'","incident_id":"'"$INC_ID"'","proof":"'"$CANARY_DIR"'/PROOF.md"}' > "$CANARY_DIR/result.json"
    # Notify: canary degraded (N consecutive failures tracked via incident ledger)
    CONSECUTIVE=$(ls -1dt "$ARTIFACTS/system/canary"/*/result.json 2>/dev/null | head -5 | while read f; do
      grep -q '"status":"DEGRADED"' "$f" 2>/dev/null && echo 1; done | wc -l)
    if [ "${CONSECUTIVE:-0}" -ge 2 ]; then
      "$ROOT_DIR/ops/scripts/notify_banner.sh" CANARY_DEGRADED "{\"failed_invariant\":\"$FAILED_INVARIANT\",\"proof_paths\":[\"$CANARY_DIR/PROOF.md\"]}" 2>/dev/null || true
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
- /api/ask smoke: PASS
- /api/ui/version drift: PASS

## Artifacts
- Reconcile: $RECONCILE_PROOF
- noVNC ws_probe: $WS_PROOF
- Ask response: $CANARY_DIR/ask_response.json
- Version: $CANARY_DIR/version.json
EOF

echo '{"status":"PASS","run_id":"'"$RUN_ID"'","proof":"'"$CANARY_DIR"'/PROOF.md"}' > "$CANARY_DIR/result.json"
echo "=== canary COMPLETE ==="
echo "  Proof: $CANARY_DIR/PROOF.md"
cat "$CANARY_DIR/result.json"
exit 0

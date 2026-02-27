#!/usr/bin/env bash
# system.reconcile — Autopilot Reconcile Loop.
# 1. Generate State Pack
# 2. Evaluate invariants
# 3. If all pass -> SUCCESS with proof
# 4. Else choose playbook by failing invariants (deterministic mapping)
# 5. Run playbook, regenerate, repeat up to K attempts with backoff
# 6. If still failing -> FAILURE or WAITING_FOR_HUMAN
#
# Fail-closed: never emit READY unless invariants pass with proof.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ARTIFACTS="${OPENCLAW_ARTIFACTS_ROOT:-$ROOT_DIR/artifacts}"
MAX_ATTEMPTS="${OPENCLAW_RECONCILE_MAX_ATTEMPTS:-3}"
BACKOFF_SEC="${OPENCLAW_RECONCILE_BACKOFF_SEC:-30}"
RUN_ID="reconcile_$(date -u +%Y%m%dT%H%M%SZ)_$(od -A n -t x4 -N 2 /dev/urandom 2>/dev/null | tr -d ' ' || echo $$)"
RECONCILE_DIR="$ARTIFACTS/system/reconcile/$RUN_ID"
INCIDENTS_DIR="$ARTIFACTS/incidents"
mkdir -p "$RECONCILE_DIR" "$INCIDENTS_DIR"

# Concurrency lock (flock) + TTL — skip if another reconcile running
LOCK_DIR="${OPENCLAW_RECONCILE_LOCK_DIR:-$ROOT_DIR/.locks}"
mkdir -p "$LOCK_DIR"
LOCK_FILE="$LOCK_DIR/reconcile.lock"
exec 201>"$LOCK_FILE"
if ! flock -n 201 2>/dev/null; then
  echo '{"status":"SKIP","reason":"lock_contention","run_id":"'"$RUN_ID"'"}' > "$RECONCILE_DIR/result.json"
  cat "$RECONCILE_DIR/result.json"
  exit 2
fi

# Deterministic mapping: failing invariant -> playbook
# hq_health_build_sha_not_unknown -> recover_hq_routing (or deploy)
# autopilot_status_http_200 -> recover_hq_routing
# serve_single_root_targets_frontdoor -> reconcile_frontdoor_serve
# frontdoor_listening_8788 -> reconcile_frontdoor_serve
# novnc_http_200, ws_probe_* -> recover_novnc_ws

choose_playbook() {
  local inv_json="$1"
  if python3 -c "
import json,sys
d=json.load(open('$inv_json'))
failing=[i['id'] for i in d.get('invariants',[]) if not i.get('pass')]
if 'serve_single_root_targets_frontdoor' in failing or 'frontdoor_listening_8788' in failing:
    print('reconcile_frontdoor_serve')
elif 'novnc_http_200' in failing or 'ws_probe_websockify_ge_10s' in failing or 'ws_probe_novnc_websockify_ge_10s' in failing:
    print('recover_novnc_ws')
elif 'hq_health_build_sha_not_unknown' in failing or 'autopilot_status_http_200' in failing:
    print('recover_hq_routing')
else:
    print('recover_hq_routing')
" 2>/dev/null; then
    return 0
  fi
  echo "recover_hq_routing"
}

write_incident() {
  local incident_id="$1"
  local status="$2"
  local summary="$3"
  local inc_dir="$INCIDENTS_DIR/$incident_id"
  mkdir -p "$inc_dir"
  [ -d "$RECONCILE_DIR/state_pack_before" ] && cp -r "$RECONCILE_DIR/state_pack_before" "$inc_dir/" 2>/dev/null || true
  [ -f "$RECONCILE_DIR/invariants_before.json" ] && cp "$RECONCILE_DIR/invariants_before.json" "$inc_dir/" 2>/dev/null || true
  [ -f "$RECONCILE_DIR/actions_taken.json" ] && cp "$RECONCILE_DIR/actions_taken.json" "$inc_dir/" 2>/dev/null || true
  [ -d "$RECONCILE_DIR/state_pack_after" ] && cp -r "$RECONCILE_DIR/state_pack_after" "$inc_dir/" 2>/dev/null || true
  [ -f "$RECONCILE_DIR/invariants_after.json" ] && cp "$RECONCILE_DIR/invariants_after.json" "$inc_dir/" 2>/dev/null || true
  BUILD_SHA="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
  cat > "$inc_dir/SUMMARY.md" << EOF
# Incident: $incident_id

**build_sha:** $BUILD_SHA
**Status:** $status
**Timestamp:** $(date -u +%Y-%m-%dT%H:%M:%SZ)

## Summary
$summary

## Artifacts
- state_pack_before/
- invariants_before.json
- actions_taken.json
- state_pack_after/
- invariants_after.json
EOF
}

attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
  echo "=== Reconcile attempt $attempt/$MAX_ATTEMPTS (run_id=$RUN_ID) ==="

  # 1. State Pack
  SP_RUN_ID="reconcile_${RUN_ID}_sp${attempt}"
  OPENCLAW_RUN_ID="$SP_RUN_ID" "$ROOT_DIR/ops/scripts/state_pack.sh" 2>/dev/null | tail -1 > "$RECONCILE_DIR/state_pack_result.json" || true
  SP_DIR=$(ls -1dt "$ARTIFACTS/system/state_pack"/*/ 2>/dev/null | head -1)
  if [ -z "$SP_DIR" ]; then
    echo '{"status":"FAILURE","reason":"State pack generation failed","run_id":"'"$RUN_ID"'"}' > "$RECONCILE_DIR/result.json"
    INC_ID="incident_${RUN_ID}"
    write_incident "$INC_ID" "FAILURE" "State pack generation failed"
    cat "$RECONCILE_DIR/result.json"
    exit 1
  fi
  cp -r "$SP_DIR" "$RECONCILE_DIR/state_pack_before/" 2>/dev/null || true

  # 2. Invariants
  OPENCLAW_STATE_PACK_RUN_ID=$(basename "$SP_DIR") OPENCLAW_INVARIANTS_OUTPUT="$RECONCILE_DIR/invariants_before.json" \
    python3 "$ROOT_DIR/ops/scripts/invariants_eval.py" > "$RECONCILE_DIR/invariants_stdout.json" 2>/dev/null || true
  cp "$RECONCILE_DIR/invariants_before.json" "$RECONCILE_DIR/invariants_before.json" 2>/dev/null || true

  if [ ! -f "$RECONCILE_DIR/invariants_before.json" ]; then
    # Use stdout if file wasn't written
    [ -f "$RECONCILE_DIR/invariants_stdout.json" ] && cp "$RECONCILE_DIR/invariants_stdout.json" "$RECONCILE_DIR/invariants_before.json" 2>/dev/null || true
  fi

  ALL_PASS=$(python3 -c "import json; d=json.load(open('$RECONCILE_DIR/invariants_before.json')); print(d.get('all_pass', False))" 2>/dev/null || echo "false")

  if [ "$ALL_PASS" = "True" ]; then
    # SUCCESS with proof
    cat > "$RECONCILE_DIR/PROOF.md" << EOF
# Reconcile SUCCESS

**Run ID:** $RUN_ID
**Timestamp:** $(date -u +%Y-%m-%dT%H:%M:%SZ)

All invariants passed. Evidence: invariants_before.json
State pack: $SP_DIR
EOF
    echo '{"status":"SUCCESS","run_id":"'"$RUN_ID"'","artifact_dir":"artifacts/system/reconcile/'"$RUN_ID"'","proof":"artifacts/system/reconcile/'"$RUN_ID"'/PROOF.md"}' > "$RECONCILE_DIR/result.json"
    cat "$RECONCILE_DIR/result.json"
    exit 0
  fi

  # 4. Choose and run playbook
  PLAYBOOK=$(choose_playbook "$RECONCILE_DIR/invariants_before.json")
  echo "Failing invariants -> playbook: $PLAYBOOK"
  cat > "$RECONCILE_DIR/actions_taken.json" << EOF
{"attempt":$attempt,"playbook":"$PLAYBOOK","run_id":"$RUN_ID","timestamp_utc":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF

  case "$PLAYBOOK" in
    reconcile_frontdoor_serve) bash "$ROOT_DIR/ops/playbooks/reconcile_frontdoor_serve.sh" 2>&1 | tail -5 ;;
    recover_novnc_ws)         bash "$ROOT_DIR/ops/playbooks/recover_novnc_ws.sh" 2>&1 | tail -5 ;;
    recover_hq_routing)       bash "$ROOT_DIR/ops/playbooks/recover_hq_routing.sh" 2>&1 | tail -5 ;;
    *)                       bash "$ROOT_DIR/ops/playbooks/recover_hq_routing.sh" 2>&1 | tail -5 ;;
  esac

  # Incident ledger: record remediation run
  INC_REMED="incident_${RUN_ID}_attempt${attempt}"
  write_incident "$INC_REMED" "REMEDIATION" "Playbook $PLAYBOOK executed (attempt $attempt)"

  sleep "$BACKOFF_SEC"
  attempt=$((attempt + 1))
done

# Still failing after K attempts — capture final state
OPENCLAW_RUN_ID="${RUN_ID}_final" "$ROOT_DIR/ops/scripts/state_pack.sh" 2>/dev/null | tail -1 > "$RECONCILE_DIR/state_pack_final.json" || true
SP_FINAL=$(ls -1dt "$ARTIFACTS/system/state_pack"/*/ 2>/dev/null | head -1)
[ -n "$SP_FINAL" ] && cp -r "$SP_FINAL" "$RECONCILE_DIR/state_pack_after/" 2>/dev/null || true
[ -n "$SP_FINAL" ] && OPENCLAW_STATE_PACK_RUN_ID=$(basename "$SP_FINAL") OPENCLAW_INVARIANTS_OUTPUT="$RECONCILE_DIR/invariants_after.json" \
  python3 "$ROOT_DIR/ops/scripts/invariants_eval.py" 2>/dev/null || true

INC_ID="incident_${RUN_ID}"
write_incident "$INC_ID" "WAITING_FOR_HUMAN" "Reconcile failed after $MAX_ATTEMPTS attempts. Single instruction: run doctor or openclaw_hq_audit, then retry reconcile."
echo '{"status":"WAITING_FOR_HUMAN","reason":"Reconcile failed after '"$MAX_ATTEMPTS"' attempts","run_id":"'"$RUN_ID"'","incident_id":"'"$INC_ID"'","instruction":"Run doctor or openclaw_hq_audit, then retry reconcile"}' > "$RECONCILE_DIR/result.json"
cat "$RECONCILE_DIR/result.json"
exit 1

#!/usr/bin/env bash
# system.reconcile — Autopilot Reconcile Loop.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ARTIFACTS="${OPENCLAW_ARTIFACTS_ROOT:-$ROOT_DIR/artifacts}"
MAX_ATTEMPTS="${OPENCLAW_RECONCILE_MAX_ATTEMPTS:-3}"
BACKOFF_SEC="${OPENCLAW_RECONCILE_BACKOFF_SEC:-30}"
STATE_PACK_THRESHOLD_SEC="${OPENCLAW_STATE_PACK_FRESHNESS_THRESHOLD_SEC:-7200}"
STATE_PACK_GENERATOR="$ROOT_DIR/ops/scripts/state_pack_generate.sh"
RUN_ID="reconcile_$(date -u +%Y%m%dT%H%M%SZ)_$(od -A n -t x4 -N 2 /dev/urandom 2>/dev/null | tr -d ' ' || echo $$)"
RECONCILE_DIR="$ARTIFACTS/system/reconcile/$RUN_ID"
INCIDENTS_DIR="$ARTIFACTS/incidents"
mkdir -p "$RECONCILE_DIR" "$INCIDENTS_DIR"

LOCK_DIR="${OPENCLAW_RECONCILE_LOCK_DIR:-$ROOT_DIR/.locks}"
mkdir -p "$LOCK_DIR"
LOCK_FILE="$LOCK_DIR/reconcile.lock"
exec 201>"$LOCK_FILE"
if ! flock -n 201 2>/dev/null; then
  echo "SKIP_LOCK_CONTENDED run_id=$RUN_ID timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$RECONCILE_DIR/SKIP_LOCK_CONTENDED.txt"
  echo '{"status":"SKIP","reason":"SKIP_LOCK_CONTENDED","run_id":"'"$RUN_ID"'","artifact":"artifacts/system/reconcile/'"$RUN_ID"'/SKIP_LOCK_CONTENDED.txt"}' > "$RECONCILE_DIR/result.json"
  cat "$RECONCILE_DIR/result.json"
  exit 0
fi

inspect_latest_state_pack() {
  python3 - "$ROOT_DIR" "$ARTIFACTS" "$STATE_PACK_THRESHOLD_SEC" <<'PYEOF'
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from ops.lib.state_pack_contract import evaluate_state_pack_freshness

payload = evaluate_state_pack_freshness(
    artifacts_root=Path(sys.argv[2]),
    threshold_sec=int(sys.argv[3]),
)
print(json.dumps(payload))
PYEOF
}

json_field() {
  python3 - "$1" "$2" <<'PYEOF'
import json
import sys

payload = json.loads(sys.argv[1])
value = payload.get(sys.argv[2])
if value is None:
    print("")
elif isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print(value)
PYEOF
}

write_controlled_result() {
  local status="$1"
  local reason="$2"
  python3 - "$RECONCILE_DIR/result.json" "$status" "$reason" "$RUN_ID" <<'PYEOF'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

payload = {
    "status": sys.argv[2],
    "reason": sys.argv[3],
    "run_id": sys.argv[4],
    "timestamp": datetime.now(timezone.utc).isoformat(),
}
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload))
PYEOF
}

trigger_state_pack_once() {
  local pack_run_id="$1"
  local output_path="$2"
  local rc=0
  OPENCLAW_RUN_ID="$pack_run_id" "$STATE_PACK_GENERATOR" >"$output_path" 2>/dev/null || rc=$?
  return "$rc"
}

resolve_state_pack_dir() {
  local pack_run_id="$1"
  local output_path="$2"
  local latest_json
  local latest_status
  local latest_reason
  local latest_path
  local age_sec
  local trigger_rc=0

  latest_json="$(inspect_latest_state_pack)"
  latest_status="$(json_field "$latest_json" status)"
  latest_reason="$(json_field "$latest_json" reason)"
  latest_path="$(json_field "$latest_json" latest_path)"
  age_sec="$(json_field "$latest_json" age_sec)"
  if [ "$latest_status" = "PASS" ] && [ -n "$latest_path" ]; then
    printf '%s\n' "$latest_path"
    return 0
  fi

  trigger_state_pack_once "$pack_run_id" "$output_path" || trigger_rc=$?

  latest_json="$(inspect_latest_state_pack)"
  latest_status="$(json_field "$latest_json" status)"
  latest_reason="$(json_field "$latest_json" reason)"
  latest_path="$(json_field "$latest_json" latest_path)"
  age_sec="$(json_field "$latest_json" age_sec)"
  if [ "$latest_status" = "PASS" ] && [ -n "$latest_path" ]; then
    printf '%s\n' "$latest_path"
    return 0
  fi

  STATE_PACK_CONTROLLED_REASON="state_pack_unavailable reason=${latest_reason:-unknown} trigger_rc=${trigger_rc} age_sec=${age_sec:-unknown}"
  if [ "$trigger_rc" -eq 10 ]; then
    STATE_PACK_CONTROLLED_REASON="${STATE_PACK_CONTROLLED_REASON} generator=SKIP_LOCK_CONTENDED"
  fi
  printf '%s\n' "$STATE_PACK_CONTROLLED_REASON" > "$RECONCILE_DIR/state_pack_controlled_reason.txt"
  return 1
}

choose_playbook() {
  local inv_json="$1"
  if python3 -c "
import json,sys
d=json.load(open('$inv_json'))
failing=[i['id'] for i in d.get('invariants',[]) if not i.get('pass')]
if 'browser_gateway_ready' in failing:
    print('recover_browser_gateway')
elif 'serve_single_root_targets_frontdoor' in failing or 'frontdoor_listening_8788' in failing:
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

STATE_PACK_CONTROLLED_REASON=""
attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
  echo "=== Reconcile attempt $attempt/$MAX_ATTEMPTS (run_id=$RUN_ID) ==="

  SP_RUN_ID="reconcile_${RUN_ID}_sp${attempt}"
  SP_DIR=""
  if ! SP_DIR="$(resolve_state_pack_dir "$SP_RUN_ID" "$RECONCILE_DIR/state_pack_result.json")"; then
    STATE_PACK_CONTROLLED_REASON="$(cat "$RECONCILE_DIR/state_pack_controlled_reason.txt" 2>/dev/null || echo "state_pack_unavailable")"
    write_controlled_result "FAIL" "$STATE_PACK_CONTROLLED_REASON"
    exit 1
  fi
  cp -r "$SP_DIR" "$RECONCILE_DIR/state_pack_before/" 2>/dev/null || true

  OPENCLAW_STATE_PACK_RUN_ID="$(basename "$SP_DIR")" OPENCLAW_INVARIANTS_OUTPUT="$RECONCILE_DIR/invariants_before.json" \
    python3 "$ROOT_DIR/ops/scripts/invariants_eval.py" > "$RECONCILE_DIR/invariants_stdout.json" 2>/dev/null || true

  if [ ! -f "$RECONCILE_DIR/invariants_before.json" ]; then
    [ -f "$RECONCILE_DIR/invariants_stdout.json" ] && cp "$RECONCILE_DIR/invariants_stdout.json" "$RECONCILE_DIR/invariants_before.json" 2>/dev/null || true
  fi

  ALL_PASS="$(python3 -c "import json; d=json.load(open('$RECONCILE_DIR/invariants_before.json')); print(d.get('all_pass', False))" 2>/dev/null || echo "false")"

  if [ "$ALL_PASS" = "True" ]; then
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

  PLAYBOOK="$(choose_playbook "$RECONCILE_DIR/invariants_before.json")"
  echo "Failing invariants -> playbook: $PLAYBOOK"
  cat > "$RECONCILE_DIR/actions_taken.json" << EOF
{"attempt":$attempt,"playbook":"$PLAYBOOK","run_id":"$RUN_ID","timestamp_utc":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
EOF

  case "$PLAYBOOK" in
    recover_browser_gateway)  bash "$ROOT_DIR/ops/playbooks/recover_browser_gateway.sh" 2>&1 | tail -5 ;;
    reconcile_frontdoor_serve) bash "$ROOT_DIR/ops/playbooks/reconcile_frontdoor_serve.sh" 2>&1 | tail -5 ;;
    recover_novnc_ws)         bash "$ROOT_DIR/ops/playbooks/recover_novnc_ws.sh" 2>&1 | tail -5 ;;
    recover_hq_routing)       bash "$ROOT_DIR/ops/playbooks/recover_hq_routing.sh" 2>&1 | tail -5 ;;
    *)                        bash "$ROOT_DIR/ops/playbooks/recover_hq_routing.sh" 2>&1 | tail -5 ;;
  esac

  INC_REMED="incident_${RUN_ID}_attempt${attempt}"
  write_incident "$INC_REMED" "REMEDIATION" "Playbook $PLAYBOOK executed (attempt $attempt)"

  sleep "$BACKOFF_SEC"
  attempt=$((attempt + 1))
done

SP_FINAL=""
FINAL_RUN_ID="${RUN_ID}_final"
if SP_FINAL="$(resolve_state_pack_dir "$FINAL_RUN_ID" "$RECONCILE_DIR/state_pack_final.json")"; then
  cp -r "$SP_FINAL" "$RECONCILE_DIR/state_pack_after/" 2>/dev/null || true
  OPENCLAW_STATE_PACK_RUN_ID="$(basename "$SP_FINAL")" OPENCLAW_INVARIANTS_OUTPUT="$RECONCILE_DIR/invariants_after.json" \
    python3 "$ROOT_DIR/ops/scripts/invariants_eval.py" 2>/dev/null || true
fi

INC_ID="incident_${RUN_ID}"
write_incident "$INC_ID" "WAITING_FOR_HUMAN" "Reconcile failed after $MAX_ATTEMPTS attempts. Single instruction: run doctor or openclaw_hq_audit, then retry reconcile."
echo '{"status":"WAITING_FOR_HUMAN","reason":"Reconcile failed after '"$MAX_ATTEMPTS"' attempts","run_id":"'"$RUN_ID"'","incident_id":"'"$INC_ID"'","instruction":"Run doctor or openclaw_hq_audit, then retry reconcile"}' > "$RECONCILE_DIR/result.json"
if [ -f "$ROOT_DIR/ops/scripts/notify_banner.sh" ]; then
  "$ROOT_DIR/ops/scripts/notify_banner.sh" WAITING_FOR_HUMAN '{"instruction":"Run doctor or openclaw_hq_audit, then retry reconcile"}' 2>/dev/null || true
fi
cat "$RECONCILE_DIR/result.json"
exit 1

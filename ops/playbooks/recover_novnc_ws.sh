#!/usr/bin/env bash
# playbook.recover_novnc_ws — Idempotent noVNC + WebSocket recovery.
# Restarts novnc, frontdoor; runs openclaw_novnc_routing_fix; verifies WSS probe.
# Emits: before/after state packs, invariants, ws_probe results.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ARTIFACTS="${OPENCLAW_ARTIFACTS_ROOT:-$ROOT_DIR/artifacts}"
RUN_ID="${OPENCLAW_RUN_ID:-recover_novnc_$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="$ARTIFACTS/playbooks/recover_novnc_ws/$RUN_ID"
mkdir -p "$OUT_DIR"

echo "=== playbook.recover_novnc_ws ($RUN_ID) ==="

# Early exit: suppress during active login window
GATE_CHECK="$ROOT_DIR/ops/scripts/csr_human_gate_check.sh"
if [ -x "$GATE_CHECK" ] && "$GATE_CHECK" soma_kajabi >/dev/null 2>&1; then
  GATE_INFO=$("$GATE_CHECK" soma_kajabi 2>/dev/null || true)
  echo "  Login window active — recovery suppressed"
  cat > "$OUT_DIR/gate_suppression.json" << GEOF
{"remediation_suppressed": true, "reason": "remediation suppressed due to active login window", "gate_info": $GATE_INFO, "timestamp_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
GEOF
  echo '{"ok":true,"suppressed":true,"reason":"active login window","artifact_dir":"artifacts/playbooks/recover_novnc_ws/'"$RUN_ID"'","run_id":"'"$RUN_ID"'"}'
  exit 0
fi

# 1. State pack before
OPENCLAW_RUN_ID="${RUN_ID}_before" "$ROOT_DIR/ops/scripts/state_pack.sh" 2>/dev/null | tail -1 > "$OUT_DIR/state_pack_before.json" || true
SP_BEFORE=$(ls -1dt "$ARTIFACTS/system/state_pack"/*/ 2>/dev/null | head -1)
[ -n "$SP_BEFORE" ] && cp -r "$SP_BEFORE" "$OUT_DIR/state_pack_before/" 2>/dev/null || true
[ -n "$SP_BEFORE" ] && OPENCLAW_STATE_PACK_RUN_ID=$(basename "$SP_BEFORE") OPENCLAW_INVARIANTS_OUTPUT="$OUT_DIR/invariants_before.json" \
  python3 "$ROOT_DIR/ops/scripts/invariants_eval.py" 2>/dev/null || true

# 1b. Hop-by-hop probe before fix (diagnostic baseline)
if [ -f "$ROOT_DIR/ops/scripts/ws_upgrade_hop_probe.sh" ]; then
  OPENCLAW_HOP_PROBE_RUN_ID="${RUN_ID}_before" \
    bash "$ROOT_DIR/ops/scripts/ws_upgrade_hop_probe.sh" > "$OUT_DIR/hop_probe_before.log" 2>&1 || true
fi

# 1c. Auto-heal: if 6080 not listening, run shm_fix before routing fix
if ! ss -tln 2>/dev/null | grep -qE ':6080[^0-9]|:6080$'; then
  echo "  Port 6080 not listening; running novnc_shm_fix.sh preflight..."
  if [ -f "$ROOT_DIR/ops/scripts/novnc_shm_fix.sh" ]; then
    bash "$ROOT_DIR/ops/scripts/novnc_shm_fix.sh" 2>&1 | tee "$OUT_DIR/shm_fix_preflight.log" || true
  fi
fi

# 2. Run routing fix (idempotent: restart novnc, frontdoor, serve)
bash "$ROOT_DIR/ops/scripts/openclaw_novnc_routing_fix.sh" 2>&1 | tee "$OUT_DIR/routing_fix.log" || true
# routing_fix can fail; we continue to capture after state

# 3. State pack + invariants after
OPENCLAW_RUN_ID="${RUN_ID}_after" "$ROOT_DIR/ops/scripts/state_pack.sh" 2>/dev/null | tail -1 > "$OUT_DIR/state_pack_after.json" || true
SP_AFTER=$(ls -1dt "$ARTIFACTS/system/state_pack"/*/ 2>/dev/null | head -1)
[ -n "$SP_AFTER" ] && cp -r "$SP_AFTER" "$OUT_DIR/state_pack_after/" 2>/dev/null || true
[ -n "$SP_AFTER" ] && OPENCLAW_STATE_PACK_RUN_ID=$(basename "$SP_AFTER") OPENCLAW_INVARIANTS_OUTPUT="$OUT_DIR/invariants_after.json" \
  python3 "$ROOT_DIR/ops/scripts/invariants_eval.py" 2>/dev/null || true

# 3b. Hop-by-hop probe after fix (validation)
if [ -f "$ROOT_DIR/ops/scripts/ws_upgrade_hop_probe.sh" ]; then
  OPENCLAW_HOP_PROBE_RUN_ID="${RUN_ID}_after" \
    bash "$ROOT_DIR/ops/scripts/ws_upgrade_hop_probe.sh" > "$OUT_DIR/hop_probe_after.log" 2>&1 || true
fi

# 3c. Verify doctor PASS after routing_fix; if not, invoke autorecover once
DOCTOR_OK=0
if [ -x "$ROOT_DIR/ops/openclaw_novnc_doctor.sh" ]; then
  OPENCLAW_RUN_ID="${RUN_ID}_verify" "$ROOT_DIR/ops/openclaw_novnc_doctor.sh" --fast > "$OUT_DIR/doctor_verify.log" 2>&1 && DOCTOR_OK=1
fi
if [ "$DOCTOR_OK" -eq 0 ] && [ -f "$ROOT_DIR/ops/scripts/novnc_autorecover.py" ]; then
  echo "  Doctor still FAIL after routing_fix; running novnc_autorecover..."
  OPENCLAW_RUN_ID="${RUN_ID}_autorecover" python3 "$ROOT_DIR/ops/scripts/novnc_autorecover.py" > "$OUT_DIR/autorecover.log" 2>&1 && DOCTOR_OK=1 || true
fi

# 4. Actions taken
ACTIONS='["hop_probe_before", "openclaw_novnc_routing_fix", "hop_probe_after"'
[ "$DOCTOR_OK" -eq 0 ] && ACTIONS="$ACTIONS, \"novnc_autorecover\""
ACTIONS="$ACTIONS]"
cat > "$OUT_DIR/actions_taken.json" << EOF
{
  "playbook": "recover_novnc_ws",
  "run_id": "$RUN_ID",
  "actions": $ACTIONS,
  "timestamp_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "Artifacts: $OUT_DIR"
echo '{"ok":true,"artifact_dir":"artifacts/playbooks/recover_novnc_ws/'"$RUN_ID"'","run_id":"'"$RUN_ID"'"}'

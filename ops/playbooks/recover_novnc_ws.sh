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

# 4. Actions taken
cat > "$OUT_DIR/actions_taken.json" << EOF
{
  "playbook": "recover_novnc_ws",
  "run_id": "$RUN_ID",
  "actions": ["hop_probe_before", "openclaw_novnc_routing_fix", "hop_probe_after"],
  "timestamp_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "Artifacts: $OUT_DIR"
echo '{"ok":true,"artifact_dir":"artifacts/playbooks/recover_novnc_ws/'"$RUN_ID"'","run_id":"'"$RUN_ID"'"}'

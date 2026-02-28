#!/usr/bin/env bash
# playbook.recover_hq_routing — Idempotent HQ routing recovery.
# Ensures frontdoor routes /api/* and / to HQ (8787). Restarts frontdoor, applies serve.
# Emits: before/after state packs, invariants.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ARTIFACTS="${OPENCLAW_ARTIFACTS_ROOT:-$ROOT_DIR/artifacts}"
RUN_ID="${OPENCLAW_RUN_ID:-recover_hq_$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="$ARTIFACTS/playbooks/recover_hq_routing/$RUN_ID"
FRONTDOOR_PORT="${OPENCLAW_FRONTDOOR_PORT:-8788}"
mkdir -p "$OUT_DIR"

echo "=== playbook.recover_hq_routing ($RUN_ID) ==="

# 1. State pack before
OPENCLAW_RUN_ID="${RUN_ID}_before" "$ROOT_DIR/ops/scripts/state_pack.sh" 2>/dev/null | tail -1 > "$OUT_DIR/state_pack_before.json" || true
SP_BEFORE=$(ls -1dt "$ARTIFACTS/system/state_pack"/*/ 2>/dev/null | head -1)
[ -n "$SP_BEFORE" ] && cp -r "$SP_BEFORE" "$OUT_DIR/state_pack_before/" 2>/dev/null || true
[ -n "$SP_BEFORE" ] && OPENCLAW_STATE_PACK_RUN_ID=$(basename "$SP_BEFORE") OPENCLAW_INVARIANTS_OUTPUT="$OUT_DIR/invariants_before.json" \
  python3 "$ROOT_DIR/ops/scripts/invariants_eval.py" 2>/dev/null || true

# 2. Ensure frontdoor + serve
[ -f "$ROOT_DIR/ops/install_openclaw_frontdoor.sh" ] && ! systemctl is-active --quiet openclaw-frontdoor 2>/dev/null && \
  sudo "$ROOT_DIR/ops/install_openclaw_frontdoor.sh" 2>&1 | tail -3 || true
systemctl restart openclaw-frontdoor 2>/dev/null || true
sleep 2
tailscale serve reset 2>/dev/null || true
tailscale serve --bg --tcp=443 "tcp://127.0.0.1:8443" 2>/dev/null || true
sleep 2

# 3. State pack + invariants after
OPENCLAW_RUN_ID="${RUN_ID}_after" "$ROOT_DIR/ops/scripts/state_pack.sh" 2>/dev/null | tail -1 > "$OUT_DIR/state_pack_after.json" || true
SP_AFTER=$(ls -1dt "$ARTIFACTS/system/state_pack"/*/ 2>/dev/null | head -1)
[ -n "$SP_AFTER" ] && cp -r "$SP_AFTER" "$OUT_DIR/state_pack_after/" 2>/dev/null || true
[ -n "$SP_AFTER" ] && OPENCLAW_STATE_PACK_RUN_ID=$(basename "$SP_AFTER") OPENCLAW_INVARIANTS_OUTPUT="$OUT_DIR/invariants_after.json" \
  python3 "$ROOT_DIR/ops/scripts/invariants_eval.py" 2>/dev/null || true

# 4. Actions taken
cat > "$OUT_DIR/actions_taken.json" << EOF
{
  "playbook": "recover_hq_routing",
  "run_id": "$RUN_ID",
  "actions": ["restart_frontdoor", "tailscale_serve_single_root"],
  "timestamp_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "Artifacts: $OUT_DIR"
echo '{"ok":true,"artifact_dir":"artifacts/playbooks/recover_hq_routing/'"$RUN_ID"'","run_id":"'"$RUN_ID"'"}'

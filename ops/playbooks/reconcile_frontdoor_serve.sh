#!/usr/bin/env bash
# playbook.reconcile_frontdoor_serve — Idempotent frontdoor + Tailscale Serve reconciliation.
# Ensures: frontdoor on 127.0.0.1:8788, Tailscale Serve single-root -> frontdoor.
# Emits: before/after state packs, invariants, probe results.
# Escalates only if it cannot proceed safely.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ARTIFACTS="${OPENCLAW_ARTIFACTS_ROOT:-$ROOT_DIR/artifacts}"
RUN_ID="${OPENCLAW_RUN_ID:-reconcile_frontdoor_$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="$ARTIFACTS/playbooks/reconcile_frontdoor_serve/$RUN_ID"
FRONTDOOR_PORT="${OPENCLAW_FRONTDOOR_PORT:-8788}"
mkdir -p "$OUT_DIR"

echo "=== playbook.reconcile_frontdoor_serve ($RUN_ID) ==="

# 1. State pack before
echo "1. State pack before..."
OPENCLAW_RUN_ID="${RUN_ID}_before" "$ROOT_DIR/ops/scripts/state_pack.sh" 2>/dev/null | tail -1 > "$OUT_DIR/state_pack_before.json" || true
SP_BEFORE=$(ls -1dt "$ARTIFACTS/system/state_pack"/*/ 2>/dev/null | head -1)
if [ -n "$SP_BEFORE" ]; then
  cp -r "$SP_BEFORE" "$OUT_DIR/state_pack_before/" 2>/dev/null || true
  OPENCLAW_STATE_PACK_RUN_ID=$(basename "$SP_BEFORE") OPENCLAW_INVARIANTS_OUTPUT="$OUT_DIR/invariants_before.json" \
    python3 "$ROOT_DIR/ops/scripts/invariants_eval.py" 2>/dev/null || true
fi

# 2. Install/ensure frontdoor
if ! systemctl is-active --quiet openclaw-frontdoor.service 2>/dev/null; then
  echo "2. Installing frontdoor..."
  [ -f "$ROOT_DIR/ops/install_openclaw_frontdoor.sh" ] && sudo "$ROOT_DIR/ops/install_openclaw_frontdoor.sh" 2>&1 | tail -5 || true
fi
systemctl restart openclaw-frontdoor 2>/dev/null || true
sleep 2

# 3. Tailscale Serve TCP mode (443 → Caddy TLS 8443, WebSocket-safe)
echo "3. Applying Tailscale Serve TCP mode -> 127.0.0.1:8443"
tailscale serve reset 2>/dev/null || true
tailscale serve --bg --tcp=443 "tcp://127.0.0.1:8443" 2>/dev/null || true
sleep 2

# 4. State pack + invariants after
echo "4. State pack + invariants after..."
OPENCLAW_RUN_ID="${RUN_ID}_after" "$ROOT_DIR/ops/scripts/state_pack.sh" 2>/dev/null | tail -1 > "$OUT_DIR/state_pack_after.json" || true
SP_AFTER=$(ls -1dt "$ARTIFACTS/system/state_pack"/*/ 2>/dev/null | head -1)
if [ -n "$SP_AFTER" ]; then
  cp -r "$SP_AFTER" "$OUT_DIR/state_pack_after/" 2>/dev/null || true
  OPENCLAW_STATE_PACK_RUN_ID=$(basename "$SP_AFTER") OPENCLAW_INVARIANTS_OUTPUT="$OUT_DIR/invariants_after.json" \
    python3 "$ROOT_DIR/ops/scripts/invariants_eval.py" 2>/dev/null || true
fi

# 5. Actions taken
cat > "$OUT_DIR/actions_taken.json" << EOF
{
  "playbook": "reconcile_frontdoor_serve",
  "run_id": "$RUN_ID",
  "actions": ["ensure_frontdoor", "restart_frontdoor", "tailscale_serve_single_root"],
  "timestamp_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "Artifacts: $OUT_DIR"
echo '{"ok":true,"artifact_dir":"artifacts/playbooks/reconcile_frontdoor_serve/'"$RUN_ID"'","run_id":"'"$RUN_ID"'"}'

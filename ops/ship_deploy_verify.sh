#!/usr/bin/env bash
# ship_deploy_verify.sh — Full proof-gated pipeline: ship (local) → deploy (aiops-1) → verify tree-to-tree.
#
# Tree-truth: Records and compares HEAD^{tree} vs origin/main^{tree} after push;
# deployed_tree_sha vs origin_main_tree_sha after deploy. Fails if any mismatch.
#
# Usage:
#   ./ops/ship_deploy_verify.sh [--skip-ship] [--skip-deploy]
#
# Deploy target resolution (no manual exports required):
#   1) ops/config/deploy_targets.json (preferred)
#   2) /etc/ai-ops-runner/deploy_target.env
#   3) Env vars OPENCLAW_AIOPS1_SSH, OPENCLAW_HQ_BASE (fallback)
#
# Writes: artifacts/hq_proofs/version_drift_truthy_live/<run_id>/PROOF.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

RUN_ID="$(date -u +%Y%m%d_%H%M%S)Z"
PROOF_DIR="$ROOT_DIR/artifacts/hq_proofs/version_drift_truthy_live/$RUN_ID"
mkdir -p "$PROOF_DIR"

SKIP_SHIP=0
SKIP_DEPLOY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-ship)   SKIP_SHIP=1; shift ;;
    --skip-deploy) SKIP_DEPLOY=1; shift ;;
    -h|--help)
      echo "Usage: ship_deploy_verify.sh [--skip-ship] [--skip-deploy]"
      exit 0
      ;;
    *) echo "ERROR: Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Resolve deploy target (fail-closed if deploy needed and unresolved)
if [ "$SKIP_DEPLOY" -eq 0 ]; then
  if ! source "$SCRIPT_DIR/scripts/resolve_deploy_target.sh"; then
    exit 1
  fi
fi
AIOPS_SSH="${OPENCLAW_AIOPS1_SSH:-}"
HQ_BASE="${OPENCLAW_HQ_BASE:-https://aiops-1.tailc75c62.ts.net}"

is_valid_ssh_target() {
  local target="$1" port=""
  # Reject whitespace/control chars that can bypass line-oriented regex checks.
  if [[ "$target" =~ [[:space:]] ]] || [[ "$target" == *$'\r'* ]] || [[ "$target" == *$'\n'* ]]; then
    return 1
  fi
  # Accept only user@host or user@host:port.
  if ! [[ "$target" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9.-]*(:[0-9]{1,5})?$ ]]; then
    return 1
  fi
  if [[ "$target" == *:* ]]; then
    port="${target##*:}"
    if (( port < 1 || port > 65535 )); then
      return 1
    fi
  fi
  return 0
}

# Fail-closed on malformed SSH target to prevent option/command injection
# via externally sourced OPENCLAW_AIOPS1_SSH values.
if [ "$SKIP_DEPLOY" -eq 0 ] && [ -n "$AIOPS_SSH" ]; then
  if ! is_valid_ssh_target "$AIOPS_SSH"; then
    echo "ERROR: Invalid OPENCLAW_AIOPS1_SSH target format: $AIOPS_SSH" >&2
    echo '{"overall":"FAIL","phase":"deploy_target_validation","run_id":"'"$RUN_ID"'"}' > "$PROOF_DIR/result.json"
    exit 1
  fi
fi

echo "=== ship_deploy_verify.sh ==="
echo "  Run ID: $RUN_ID"
echo "  Proof:  $PROOF_DIR"
echo ""

# --- Phase 1: Ship (local, push-capable host only) ---
HEAD_SHA=""
TREE_SHA=""
ORIGIN_MAIN_HEAD=""
ORIGIN_MAIN_TREE=""

if [ "$SKIP_SHIP" -eq 0 ]; then
  echo "==> Phase 1: Ship (push to origin/main)"
  if echo "$(hostname 2>/dev/null || echo)" | grep -qi "aiops-1"; then
    echo "  SKIP: ship must not run on production"
  elif [ -f "$SCRIPT_DIR/ship.sh" ]; then
    if ! "$SCRIPT_DIR/ship.sh" 2>&1 | tee "$PROOF_DIR/ship.log"; then
      echo "ERROR: ship.sh failed" >&2
      echo '{"overall":"FAIL","phase":"ship","run_id":"'"$RUN_ID"'"}' > "$PROOF_DIR/result.json"
      exit 2
    fi
    echo "  Ship: PASS"
  elif [ -f "$SCRIPT_DIR/ship_pipeline.sh" ]; then
    if ! "$SCRIPT_DIR/ship_pipeline.sh" 2>&1 | tee "$PROOF_DIR/ship.log"; then
      echo "ERROR: ship_pipeline failed" >&2
      echo '{"overall":"FAIL","phase":"ship","run_id":"'"$RUN_ID"'"}' > "$PROOF_DIR/result.json"
      exit 2
    fi
    echo "  Ship: PASS"
  else
    echo "  SKIP: ship.sh / ship_pipeline.sh not found"
  fi
  echo ""
fi

# --- Capture post-push tree (local or after ship) ---
git fetch origin main 2>/dev/null || true
ORIGIN_MAIN_HEAD="$(git rev-parse origin/main 2>/dev/null | head -c 40)" || ORIGIN_MAIN_HEAD=""
ORIGIN_MAIN_TREE="$(git rev-parse origin/main^{tree} 2>/dev/null | head -c 40)" || ORIGIN_MAIN_TREE=""
HEAD_SHA="$(git rev-parse HEAD 2>/dev/null | head -c 40)" || HEAD_SHA=""
TREE_SHA="$(git rev-parse HEAD^{tree} 2>/dev/null | head -c 40)" || TREE_SHA=""

# Verify local HEAD^{tree} == origin/main^{tree} after push
if [ -n "$TREE_SHA" ] && [ -n "$ORIGIN_MAIN_TREE" ]; then
  if [ "$TREE_SHA" != "$ORIGIN_MAIN_TREE" ]; then
    echo "ERROR: Local HEAD^{tree} ($TREE_SHA) != origin/main^{tree} ($ORIGIN_MAIN_TREE) after ship" >&2
    echo '{"overall":"FAIL","phase":"tree_verify_post_ship","run_id":"'"$RUN_ID"'"}' > "$PROOF_DIR/result.json"
    exit 2
  fi
  echo "  Tree verify (post-ship): HEAD^{tree} == origin/main^{tree} == ${TREE_SHA:0:12}..."
fi
echo ""

# --- Phase 2: Deploy (on aiops-1) ---
if [ "$SKIP_DEPLOY" -eq 0 ] && [ -n "$AIOPS_SSH" ]; then
  echo "==> Phase 2: Deploy on aiops-1"
  if ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -o BatchMode=yes -- "$AIOPS_SSH" bash -se <<'REMOTE_DEPLOY' 2>&1 | tee "$PROOF_DIR/deploy.log"; then
cd /opt/ai-ops-runner
git fetch origin main
git reset --hard origin/main
sudo ./ops/deploy_until_green.sh 2>/dev/null || sudo ./ops/deploy_pipeline.sh
REMOTE_DEPLOY
    echo "  Deploy: PASS"
  else
    echo "ERROR: Deploy failed on aiops-1" >&2
    echo '{"overall":"FAIL","phase":"deploy","run_id":"'"$RUN_ID"'"}' > "$PROOF_DIR/result.json"
    exit 2
  fi
  echo ""
  # --- Phase 2a: Ensure noVNC readiness (shm_fix if needed, wait for 6080) ---
  echo "==> Phase 2a: Ensure noVNC readiness (shm_fix + 6080 wait)"
  ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -o BatchMode=yes -- "$AIOPS_SSH" bash -se <<'REMOTE_NOVNC' 2>&1 | tail -8 || true
cd /opt/ai-ops-runner
if ! ss -tln 2>/dev/null | grep -qE ':6080[^0-9]|6080 '; then
  sudo bash ./ops/scripts/novnc_shm_fix.sh 2>&1 | tail -5
  for _i in $(seq 1 30); do
    ss -tln 2>/dev/null | grep -qE ':6080[^0-9]|6080 ' && break
    systemctl start openclaw-novnc 2>/dev/null || true
    sleep 2
  done
fi
REMOTE_NOVNC
  sleep 5
  echo ""
  # --- Phase 2b: Force-run strict canary (require PASS with noVNC up) ---
  echo "==> Phase 2b: Strict canary (noVNC 6080 + WSS >=10s)"
  if ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -o BatchMode=yes -- "$AIOPS_SSH" bash -se <<'REMOTE_CANARY' 2>&1 | tee "$PROOF_DIR/canary.log"; then
cd /opt/ai-ops-runner
./ops/scripts/canary.sh
REMOTE_CANARY
    echo "  Canary: PASS"
  else
    echo "ERROR: Strict canary failed (noVNC must be up)" >&2
    # Collect hop-by-hop WebSocket upgrade diagnostics on canary failure
    echo "==> Phase 2b-diag: Hop-by-hop WebSocket upgrade probe"
    ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -o BatchMode=yes -- "$AIOPS_SSH" bash -se <<'REMOTE_HOP_PROBE' 2>&1 | tee "$PROOF_DIR/hop_probes.log" || true
cd /opt/ai-ops-runner
[ -f ops/scripts/ws_upgrade_hop_probe.sh ] && bash ops/scripts/ws_upgrade_hop_probe.sh 2>&1 || echo "hop_probe script not found"
REMOTE_HOP_PROBE
    echo '{"overall":"FAIL","phase":"canary","run_id":"'"$RUN_ID"'"}' > "$PROOF_DIR/result.json"
    exit 2
  fi
  echo ""
elif [ "$SKIP_DEPLOY" -eq 0 ] && [ -z "$AIOPS_SSH" ]; then
  echo "==> Phase 2: Deploy (SKIP — set OPENCLAW_AIOPS1_SSH to run deploy)"
  echo ""
fi

# --- Phase 3: Verify tree-to-tree via /api/ui/version ---
echo "==> Phase 3: Verify tree-to-tree (deployed vs origin/main)"
sleep 5
VERSION_JSON=""
if curl -sf --connect-timeout 10 --max-time 15 "$HQ_BASE/api/ui/version" 2>/dev/null > "$PROOF_DIR/version.json"; then
  VERSION_JSON="$(cat "$PROOF_DIR/version.json")"
fi

if [ -z "$VERSION_JSON" ]; then
  echo "ERROR: /api/ui/version unreachable at $HQ_BASE" >&2
  echo '{"overall":"FAIL","phase":"version_check","run_id":"'"$RUN_ID"'"}' > "$PROOF_DIR/result.json"
  exit 2
fi

# Fail-closed: drift_status=unknown or drift=true -> FAIL
DRIFT_STATUS="$(echo "$VERSION_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('drift_status','unknown'))" 2>/dev/null)" || DRIFT_STATUS="unknown"
DRIFT="$(echo "$VERSION_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); v=d.get('drift'); print('true' if v is True else 'false' if v is False else 'unknown')" 2>/dev/null)" || DRIFT="unknown"
DEPLOYED_HEAD="$(echo "$VERSION_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('deployed_head_sha','') or '')" 2>/dev/null)" || DEPLOYED_HEAD=""
DEPLOYED_TREE="$(echo "$VERSION_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('deployed_tree_sha','') or '')" 2>/dev/null)" || DEPLOYED_TREE=""
ORIGIN_TREE="$(echo "$VERSION_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('origin_main_tree_sha','') or '')" 2>/dev/null)" || ORIGIN_TREE=""

if [ "$DRIFT_STATUS" = "unknown" ]; then
  echo "ERROR: Drift status unknown (origin_main_tree_sha unavailable or ship_info stale). deployed_tree_sha=$DEPLOYED_TREE origin_main_tree_sha=$ORIGIN_TREE" >&2
  echo '{"overall":"FAIL","phase":"tree_verify","drift_status":"unknown","run_id":"'"$RUN_ID"'"}' > "$PROOF_DIR/result.json"
  exit 2
fi

if [ -z "$ORIGIN_TREE" ] || [ "$ORIGIN_TREE" = "null" ]; then
  echo "ERROR: origin_main_tree_sha must not be null (ship_info.json must be deployed)." >&2
  echo '{"overall":"FAIL","phase":"tree_verify","origin_main_tree_sha":"null","run_id":"'"$RUN_ID"'"}' > "$PROOF_DIR/result.json"
  exit 2
fi

if [ "$DRIFT" = "true" ] || [ "$DRIFT" = "True" ]; then
  echo "ERROR: Drift detected. deployed_tree_sha=$DEPLOYED_TREE origin_main_tree_sha=$ORIGIN_TREE" >&2
  echo '{"overall":"FAIL","phase":"tree_verify","drift":true,"run_id":"'"$RUN_ID"'"}' > "$PROOF_DIR/result.json"
  exit 2
fi

if [ -n "$DEPLOYED_TREE" ] && [ -n "$ORIGIN_TREE" ] && [ "$DEPLOYED_TREE" != "$ORIGIN_TREE" ]; then
  echo "ERROR: deployed_tree_sha != origin_main_tree_sha" >&2
  echo '{"overall":"FAIL","phase":"tree_verify","run_id":"'"$RUN_ID"'"}' > "$PROOF_DIR/result.json"
  exit 2
fi

echo "  Tree verify: deployed_tree_sha == origin_main_tree_sha (drift=false)"
echo ""

# --- Write PROOF.md ---
cat > "$PROOF_DIR/PROOF.md" << EOF
# ship_deploy_verify PROOF

**Run ID:** $RUN_ID
**Timestamp:** $(date -u +%Y-%m-%dT%H:%M:%SZ)
**Result:** PASS

## Tree-truth verification

| Field | Value (truncated) |
|-------|-------------------|
| origin_main_head_sha | ${ORIGIN_MAIN_HEAD:0:12}... |
| origin_main_tree_sha | ${ORIGIN_MAIN_TREE:0:12}... |
| deployed_head_sha | ${DEPLOYED_HEAD:0:12}... |
| deployed_tree_sha | ${DEPLOYED_TREE:0:12}... |
| drift | false |

## Artifacts

- version.json: $PROOF_DIR/version.json
- ship.log: $PROOF_DIR/ship.log (if ran)
- deploy.log: $PROOF_DIR/deploy.log (if ran)
EOF

echo '{"overall":"PASS","run_id":"'"$RUN_ID"'","proof":"'"$PROOF_DIR"'/PROOF.md"}' > "$PROOF_DIR/result.json"

echo "=== ship_deploy_verify COMPLETE ==="
echo "  Proof: $PROOF_DIR/PROOF.md"
exit 0

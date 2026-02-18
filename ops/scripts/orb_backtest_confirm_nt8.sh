#!/usr/bin/env bash
# Tier-2 confirm_nt8 action — delegates to tools/tier2_confirm_entrypoint.py.
# Requires OPENCLAW_RUN_ID. Optional OPENCLAW_ORB_CONFIRM_SPEC_PATH.
# Exit 3 = NT8_AUTOMATION_NOT_IMPLEMENTED (Phase-0 stub, artifacts written).
set -euo pipefail
ROOT="${OPENCLAW_REPO_ROOT:-/opt/ai-ops-runner}"
RUN_ID="${OPENCLAW_RUN_ID:-}"
if [[ -z "$RUN_ID" ]]; then
  echo "NEEDS_HUMAN: OPENCLAW_RUN_ID not set"
  exit 2
fi
SPEC_PATH="${OPENCLAW_ORB_CONFIRM_SPEC_PATH:-}"
ART_ROOT="${ROOT}/artifacts/backtests/${RUN_ID}"
MODE="${OPENCLAW_ORB_CONFIRM_MODE:-strategy_analyzer}"
NT8_DIR="${OPENCLAW_NT8_USER_DIR:-}"

export BACKTEST_ONLY=true

ARGS=(
  --topk "${SPEC_PATH}"
  --output-dir "${ART_ROOT}"
  --mode "${MODE}"
)
if [[ -n "$NT8_DIR" ]]; then
  ARGS+=(--nt8-user-dir "$NT8_DIR")
fi

if [[ -z "$SPEC_PATH" || ! -f "$SPEC_PATH" ]]; then
  echo "NEEDS_HUMAN: topk.json not found at OPENCLAW_ORB_CONFIRM_SPEC_PATH ($SPEC_PATH)"
  mkdir -p "${ART_ROOT}/tier2"
  echo '{"done":true,"status":"SPEC_NOT_FOUND","exit_code":2}' > "${ART_ROOT}/tier2/done.json"
  exit 2
fi

cd "$ROOT"
exec python3 -m tools.tier2_confirm_entrypoint "${ARGS[@]}"

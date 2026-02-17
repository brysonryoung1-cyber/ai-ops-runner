#!/usr/bin/env bash
# Tier-2 confirm_nt8 action (stub). Validates spec, writes artifact skeleton, done.json with NT8_EXECUTOR_NOT_CONFIGURED.
# Requires OPENCLAW_RUN_ID. Optional OPENCLAW_ORB_CONFIRM_SPEC_PATH.
# Exit 3 = NT8_EXECUTOR_NOT_CONFIGURED (fail-closed). Binds localhost-only.
set -euo pipefail
ROOT="${OPENCLAW_REPO_ROOT:-/opt/ai-ops-runner}"
RUN_ID="${OPENCLAW_RUN_ID:-}"
if [[ -z "$RUN_ID" ]]; then
  echo "NEEDS_HUMAN: OPENCLAW_RUN_ID not set"
  exit 2
fi
ART_ROOT="${ROOT}/artifacts/backtests/${RUN_ID}/tier2"
mkdir -p "$ART_ROOT"/{raw_exports,logs}
SPEC_PATH="${OPENCLAW_ORB_CONFIRM_SPEC_PATH:-}"
# Validate: if spec provided, basic JSON check
if [[ -n "$SPEC_PATH" && -f "$SPEC_PATH" ]]; then
  if ! python3 -c "import json; json.load(open('$SPEC_PATH'))" 2>/dev/null; then
    echo "NEEDS_HUMAN: Invalid confirm spec JSON: $SPEC_PATH"
    echo '{"done":true,"status":"FAIL","exit_code":2,"reason":"invalid_spec"}' > "${ART_ROOT}/done.json"
    exit 2
  fi
fi
# Skeleton artifacts (contract-compliant)
echo "candidate_id,strategy_name,instrument,pnl,pf,sharpe,maxdd,trades,winrate,avg_trade,avg_win,avg_loss,profit_factor,status" > "${ART_ROOT}/results.csv"
echo '{"schema_version":"tier2_summary.v1","exp_id":"'"${RUN_ID}"'","verdict":"NT8_EXECUTOR_NOT_CONFIGURED","reasons":["NT8_EXECUTOR_NOT_CONFIGURED"]}' > "${ART_ROOT}/summary.json"
echo '[]' > "${ART_ROOT}/run_manifest.json"
echo '{"done":true,"status":"NT8_EXECUTOR_NOT_CONFIGURED","exit_code":3,"exp_id":"'"${RUN_ID}"'"}' > "${ART_ROOT}/done.json"
echo "orb.backtest.confirm_nt8|run_id=${RUN_ID}|status=NT8_EXECUTOR_NOT_CONFIGURED|artifacts=${ART_ROOT}"
exit 3

#!/usr/bin/env bash
# Tier-1 bulk backtest action. Writes to artifacts/backtests/<run_id>/tier1/.
# Requires OPENCLAW_RUN_ID; optional OPENCLAW_ORB_REPO_PATH, OPENCLAW_ORB_SPEC_PATH.
# Binds localhost-only; no public ingress.
set -euo pipefail
ROOT="${OPENCLAW_REPO_ROOT:-/opt/ai-ops-runner}"
RUN_ID="${OPENCLAW_RUN_ID:-}"
if [[ -z "$RUN_ID" ]]; then
  echo "NEEDS_HUMAN: OPENCLAW_RUN_ID not set"
  exit 1
fi
ART_ROOT="${ROOT}/artifacts/backtests/${RUN_ID}/tier1"
mkdir -p "$ART_ROOT"
ORB_PATH="${OPENCLAW_ORB_REPO_PATH:-/opt/algo-nt8-orb}"
SPEC_PATH="${OPENCLAW_ORB_SPEC_PATH:-}"
if [[ -z "$SPEC_PATH" ]]; then
  SPEC_PATH="${ORB_PATH}/ops/backtests/fixtures/sample_spec.json"
fi
if [[ ! -d "$ORB_PATH" ]]; then
  echo "NEEDS_HUMAN: algo-nt8-orb not found at OPENCLAW_ORB_REPO_PATH ($ORB_PATH). Clone repo or set OPENCLAW_ORB_REPO_PATH."
  echo '{"status":"error","reason":"ORB_REPO_NOT_FOUND"}' > "${ART_ROOT}/run_manifest.json"
  exit 1
fi
if [[ ! -f "$SPEC_PATH" ]]; then
  echo "NEEDS_HUMAN: Spec not found: $SPEC_PATH"
  echo '{"status":"error","reason":"SPEC_NOT_FOUND"}' > "${ART_ROOT}/run_manifest.json"
  exit 1
fi
BULK_TMP="/tmp/orb_bulk_$$"
mkdir -p "$BULK_TMP"
cd "$ORB_PATH"
python3 -m ops.backtests.run_bulk --spec "$SPEC_PATH" --artifacts-root "$BULK_TMP" 2>&1 || true
BULK_OUT="$BULK_TMP/backtests"
if [[ -d "$BULK_OUT" ]]; then
  for sub in "$BULK_OUT"/*/; do
    if [[ -d "$sub" ]]; then
      cp -r "$sub"* "$ART_ROOT/" 2>/dev/null || true
      break
    fi
  done
fi
rm -rf "$BULK_TMP"
if [[ -f "${ART_ROOT}/summary.json" ]]; then
  if [[ ! -f "${ART_ROOT}/SUMMARY.md" ]]; then
    echo "# ORB Tier-1 Bulk Backtest" > "${ART_ROOT}/SUMMARY.md"
    echo "" >> "${ART_ROOT}/SUMMARY.md"
    echo "- **run_id**: ${RUN_ID}" >> "${ART_ROOT}/SUMMARY.md"
    echo "- **artifacts**: ${ART_ROOT}" >> "${ART_ROOT}/SUMMARY.md"
  fi
  echo "orb.backtest.bulk|run_id=${RUN_ID}|artifacts=${ART_ROOT}"
  exit 0
fi
echo '{"status":"error","reason":"BULK_RUN_FAILED"}' > "${ART_ROOT}/run_manifest.json"
exit 1

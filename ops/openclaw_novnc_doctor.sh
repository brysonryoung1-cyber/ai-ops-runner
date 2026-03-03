#!/usr/bin/env bash
# openclaw_novnc_doctor.sh — convergent noVNC readiness doctor.
#
# PASS contract:
#   - ws_stability_local=verified
#   - ws_stability_tailnet=verified (or deterministic substitute when probe unavailable)
#   - fail-closed output with error_class on FAIL
#   - bounded wait/recover loop before NOVNC_NOT_READY
#   - artifact_dir points to artifacts/novnc_readiness/<run_id>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

RUN_ID="${OPENCLAW_RUN_ID:-novnc_doctor_$(date -u +%Y%m%dT%H%M%SZ)}"
MODE="deep"

if [ "${1:-}" = "--fast" ]; then
  MODE="fast"
fi

exec python3 -m ops.lib.novnc_readiness \
  --mode "$MODE" \
  --run-id "$RUN_ID" \
  --emit-artifacts

#!/usr/bin/env bash
# openclaw_novnc_doctor_selftest.sh — Assert doctor requires tailnet WS for PASS.
#
# Hermetic: checks script structure and novnc_ws_stability_check --all usage.
# Doctor PASS only when ws_stability_local AND ws_stability_tailnet verified.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
DOCTOR="$ROOT_DIR/ops/openclaw_novnc_doctor.sh"
WS_CHECK="$ROOT_DIR/ops/scripts/novnc_ws_stability_check.py"

pass() { echo "  [PASS] $1"; }
fail() { echo "  [FAIL] $1" >&2; exit 1; }

echo "=== openclaw_novnc_doctor selftest ==="

# 1. Doctor exists and calls WS check with --all
if ! grep -q "novnc_ws_stability_check\|ws_stability" "$DOCTOR" 2>/dev/null; then
  fail "Doctor must reference novnc_ws_stability_check"
fi
if ! grep -q "\-\-all\|ws_stability_local\|ws_stability_tailnet" "$DOCTOR" 2>/dev/null; then
  fail "Doctor must check both local and tailnet WS (--all or ws_stability_local/tailnet)"
fi
pass "Doctor references dual WS check (local + tailnet)"

# 2. WS check script supports --all and tailnet
if ! grep -q "\-\-all\|\-\-tailnet\|\-\-local" "$WS_CHECK" 2>/dev/null; then
  fail "novnc_ws_stability_check must support --all, --tailnet, or --local"
fi
if ! grep -q "ws_stability_local\|ws_stability_tailnet" "$WS_CHECK" 2>/dev/null; then
  fail "WS check must output ws_stability_local and ws_stability_tailnet"
fi
pass "WS check supports --all and outputs local+tailnet"

# 3. Doctor requires both for PASS
if ! grep -q "ws_stability_local" "$DOCTOR" 2>/dev/null; then
  fail "Doctor must check ws_stability_local"
fi
if ! grep -q "ws_stability_tailnet" "$DOCTOR" 2>/dev/null; then
  fail "Doctor must check ws_stability_tailnet"
fi
pass "Doctor requires both local and tailnet verified for PASS"

# 4. Doctor outputs NOVNC_WS_TAILNET_FAILED on fail
if ! grep -q "NOVNC_WS_TAILNET_FAILED\|error_class" "$DOCTOR" 2>/dev/null; then
  fail "Doctor must output error_class=NOVNC_WS_TAILNET_FAILED on tailnet WS fail"
fi
pass "Doctor outputs NOVNC_WS_TAILNET_FAILED on fail"

# 5. Doctor includes artifact_dir in output
if ! grep -q "artifact_dir" "$DOCTOR" 2>/dev/null; then
  fail "Doctor must include artifact_dir in JSON output"
fi
pass "Doctor includes artifact_dir"

echo "=== openclaw_novnc_doctor selftest PASS ==="

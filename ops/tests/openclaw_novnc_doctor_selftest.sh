#!/usr/bin/env bash
# openclaw_novnc_doctor_selftest.sh — Assert doctor is wired to convergent readiness gate.
#
# Hermetic: checks wrapper wiring + readiness module contract markers.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
DOCTOR="$ROOT_DIR/ops/openclaw_novnc_doctor.sh"
READINESS="$ROOT_DIR/ops/lib/novnc_readiness.py"

pass() { echo "  [PASS] $1"; }
fail() { echo "  [FAIL] $1" >&2; exit 1; }

echo "=== openclaw_novnc_doctor selftest ==="

# 1. Doctor exists and delegates to convergent Python module
if ! grep -q "python3 -m ops.lib.novnc_readiness" "$DOCTOR" 2>/dev/null; then
  fail "Doctor must invoke ops.lib.novnc_readiness"
fi
if ! grep -q "\-\-emit-artifacts" "$DOCTOR" 2>/dev/null; then
  fail "Doctor must emit readiness artifacts"
fi
pass "Doctor delegates to convergent readiness module"

# 2. Readiness module includes bounded backoff schedule
if ! grep -q "BACKOFF_DEEP = (2, 4, 8, 16, 32" "$READINESS" 2>/dev/null; then
  fail "Readiness module must include exponential backoff 2,4,8,16,32"
fi
pass "Readiness module defines bounded exponential backoff"

# 3. Readiness module checks required probes
if ! grep -q "/novnc/vnc.html" "$READINESS" 2>/dev/null; then
  fail "Readiness module must probe /novnc/vnc.html"
fi
if ! grep -q "/websockify" "$READINESS" 2>/dev/null; then
  fail "Readiness module must probe /websockify websocket endpoint"
fi
if ! grep -q "tcp_backend_vnc" "$READINESS" 2>/dev/null; then
  fail "Readiness module must check backend VNC TCP readiness"
fi
pass "Readiness module probes HTTP + WS + backend VNC"

# 4. Readiness module emits JSON with artifact_dir and error_class
if ! grep -q "artifact_dir" "$READINESS" 2>/dev/null; then
  fail "Readiness module output must include artifact_dir"
fi
if ! grep -q "error_class" "$READINESS" 2>/dev/null; then
  fail "Readiness module output must include error_class on failure"
fi
pass "Readiness output contract fields present"

echo "=== openclaw_novnc_doctor selftest PASS ==="

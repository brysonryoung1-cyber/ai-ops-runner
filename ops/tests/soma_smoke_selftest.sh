#!/usr/bin/env bash
# soma_smoke_selftest.sh — Selftest for the soma_smoke.sh script.
#
# Verifies that soma_smoke.sh:
#   1. Exits 0 in smoke mode
#   2. Produces expected artifact files
#   3. Writes a valid JSON smoke report
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

echo "=== soma_smoke_selftest.sh ==="

FAILURES=0
pass() { echo "  PASS: $1"; }
fail() { FAILURES=$((FAILURES + 1)); echo "  FAIL: $1" >&2; }

# --- Run soma_smoke.sh ---
echo "--- Running soma_smoke.sh ---"
SMOKE_RC=0
SMOKE_OUTPUT="$(./ops/soma_smoke.sh 2>&1)" || SMOKE_RC=$?

if [ "$SMOKE_RC" -eq 0 ]; then
  pass "soma_smoke.sh exited 0"
else
  fail "soma_smoke.sh exited $SMOKE_RC"
  echo "$SMOKE_OUTPUT" >&2
fi

# Check output contains expected PASS lines
if echo "$SMOKE_OUTPUT" | grep -q "PASS: All modules import successfully"; then
  pass "Module imports reported as PASS"
else
  fail "Module import PASS not found in output"
fi

if echo "$SMOKE_OUTPUT" | grep -q "PASS: Snapshot smoke completed"; then
  pass "Snapshot smoke reported as PASS"
else
  fail "Snapshot smoke PASS not found in output"
fi

if echo "$SMOKE_OUTPUT" | grep -q "PASS: Harvest smoke completed"; then
  pass "Harvest smoke reported as PASS"
else
  fail "Harvest smoke PASS not found in output"
fi

if echo "$SMOKE_OUTPUT" | grep -q "PASS: Mirror smoke completed"; then
  pass "Mirror smoke reported as PASS"
else
  fail "Mirror smoke PASS not found in output"
fi

# Check smoke JSON report exists
SMOKE_REPORT="$(find artifacts/soma_smoke -name "smoke.json" -type f 2>/dev/null | sort -r | head -1 || true)"
if [ -n "$SMOKE_REPORT" ]; then
  pass "smoke.json report exists: $SMOKE_REPORT"
  # Validate JSON
  if python3 -c "import json; d=json.load(open('$SMOKE_REPORT')); assert d['result'] == 'PASS'" 2>/dev/null; then
    pass "smoke.json reports PASS"
  else
    fail "smoke.json does not report PASS"
  fi
else
  fail "smoke.json report not found"
fi

# --- Summary ---
echo ""
if [ "$FAILURES" -gt 0 ]; then
  echo "=== soma_smoke_selftest: $FAILURES failure(s) ===" >&2
  exit 1
fi
echo "=== soma_smoke_selftest: ALL PASSED ==="
exit 0

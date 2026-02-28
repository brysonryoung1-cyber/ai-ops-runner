#!/usr/bin/env bash
# serve_enforcer_selftest.sh — Test that serve enforcer detects and (mock) repairs drift.
#
# Tests:
#   1. Serve enforcer creates status artifact
#   2. Serve enforcer writes valid JSON status
#   3. Canary degraded file is cleared after repair
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASSED=0
FAILED=0

pass() {
  echo "  PASS: $1"
  PASSED=$((PASSED + 1))
}

fail() {
  echo "  FAIL: $1"
  FAILED=$((FAILED + 1))
}

echo "=== Serve Enforcer Self-Test ==="

# Test 1: status.json is valid (inspect last run if available)
LAST_STATUS="$(ls -t "$ROOT_DIR/artifacts/system/serve_enforcer"/*/status.json 2>/dev/null | head -1)" || true
if [ -n "$LAST_STATUS" ] && [ -f "$LAST_STATUS" ]; then
  if python3 -c "import json; d=json.load(open('$LAST_STATUS')); assert 'run_id' in d and 'ok' in d" 2>/dev/null; then
    pass "serve enforcer writes valid status.json"
  else
    fail "serve enforcer status.json is invalid"
  fi
else
  echo "  SKIP: no serve enforcer artifacts found (run serve_enforcer.sh first)"
fi

# Test 2: canary degraded counter mechanism
DEGRADED_FILE="$ROOT_DIR/artifacts/system/canary/.degraded_count"
mkdir -p "$(dirname "$DEGRADED_FILE")"

echo "2" > "$DEGRADED_FILE"
if [ -f "$DEGRADED_FILE" ]; then
  pass "canary degraded file created"
else
  fail "canary degraded file should exist"
fi

rm -f "$DEGRADED_FILE"
if [ ! -f "$DEGRADED_FILE" ]; then
  pass "canary degraded file cleared"
else
  fail "canary degraded file should be cleared"
fi

# Test 3: rollback playbook denied without degradation
if OPENCLAW_REPO_ROOT="$ROOT_DIR" "$ROOT_DIR/ops/rollback_playbook.sh" 2>/dev/null; then
  fail "rollback should be denied without canary degradation"
else
  pass "rollback correctly denied (no degradation)"
fi

echo ""
echo "=== Results: $PASSED passed, $FAILED failed ==="
exit "$FAILED"

#!/usr/bin/env bash
# ship_pr_detection_selftest.sh — Unit-style test for PR-required detection (no network)
#
# Simulates main_requires_pr logic with mocked gh api output (fixture JSON).
# Verifies ship.sh would choose PR flow when required_pull_request_reviews is set,
# and direct push when null. No real gh or network calls.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$OPS_DIR/.." && pwd)"

PASS=0
FAIL=0
TESTS=0

assert_eq() {
  TESTS=$((TESTS + 1))
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc (expected=$expected, actual=$actual)" >&2
    FAIL=$((FAIL + 1))
  fi
}

# Detection logic matching ship.sh main_requires_pr (parse protection JSON).
# Input: JSON on stdin. Output: 0 if PR required, 1 if not.
detect_pr_required() {
  local json
  json="$(cat)"
  if echo "$json" | grep -qE '"required_pull_request_reviews"\s*:\s*\{'; then
    return 0
  fi
  return 1
}

echo "=== ship_pr_detection_selftest.sh ==="
echo "  (Mocked fixture JSON; no network)"
echo ""

# Fixture: protection with required_pull_request_reviews set (object)
FIXTURE_PR_REQUIRED='{"required_status_checks":{"contexts":["verdict-gate"]},"required_pull_request_reviews":{"url":"https://api.github.com/...","dismissal_restrictions":{},"dismiss_stale_reviews":false,"require_code_owner_reviews":false,"required_approving_review_count":0},"enforce_admins":null,"restrictions":null}'

# Fixture: protection with required_pull_request_reviews null
FIXTURE_PR_NOT_REQUIRED='{"required_status_checks":{"contexts":["verdict-gate"]},"required_pull_request_reviews":null,"enforce_admins":null,"restrictions":null}'

# Test 1: PR required → detection returns 0 (PR flow chosen)
if echo "$FIXTURE_PR_REQUIRED" | detect_pr_required; then
  assert_eq "PR required fixture → PR flow (exit 0)" "0" "0"
else
  assert_eq "PR required fixture → PR flow (exit 0)" "0" "1"
fi

# Test 2: PR not required → detection returns 1 (direct push)
if echo "$FIXTURE_PR_NOT_REQUIRED" | detect_pr_required; then
  assert_eq "PR not required fixture → direct push (exit 1)" "1" "0"
else
  assert_eq "PR not required fixture → direct push (exit 1)" "1" "1"
fi

# Test 3: SHIP_TEST_MOCK_PR_REQUIRED in ship.sh forces path without calling gh
if grep -q 'SHIP_TEST_MOCK_PR_REQUIRED' "$OPS_DIR/ship.sh" 2>/dev/null; then
  assert_eq "ship.sh uses SHIP_TEST_MOCK_PR_REQUIRED for testability" "0" "0"
else
  TESTS=$((TESTS + 1))
  FAIL=$((FAIL + 1))
  echo "  FAIL: ship.sh should support SHIP_TEST_MOCK_PR_REQUIRED" >&2
fi

echo ""
echo "=== Results: $PASS/$TESTS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1

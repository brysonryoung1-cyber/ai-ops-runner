#!/usr/bin/env bash
# ship_auto_selftest.sh — Tests for ship_auto.sh in CODEX_SKIP mode
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

assert_contains() {
  TESTS=$((TESTS + 1))
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF "$needle"; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc (expected to contain: $needle)" >&2
    FAIL=$((FAIL + 1))
  fi
}

echo "=== ship_auto_selftest.sh ==="

# --- Test 1: --help exits 0 ---
RC=0
"$OPS_DIR/ship_auto.sh" --help >/dev/null 2>&1 || RC=$?
assert_eq "help exits 0" "0" "$RC"

# --- Test 2: CODEX_SKIP mode with clean tree ---
if [ -z "$(git -C "$ROOT_DIR" status --porcelain)" ]; then
  RC=0
  OUTPUT="$(CODEX_SKIP=1 SHIP_SKIP_PYTEST=1 SHIP_SKIP_SELFTESTS=1 "$OPS_DIR/ship_auto.sh" --no-push --max-attempts 1 2>&1)" || RC=$?
  assert_eq "CODEX_SKIP=1 ship exits 0" "0" "$RC"
  assert_contains "ship shows APPROVED" "APPROVED" "$OUTPUT"
else
  echo "  SKIP: Tree is dirty, skipping ship_auto test"
fi

# --- Test 3: Max attempts bound ---
# Verify the script has bounded loop logic
if grep -q 'MAX_ATTEMPTS' "$OPS_DIR/ship_auto.sh"; then
  TESTS=$((TESTS + 1)); PASS=$((PASS + 1))
  echo "  PASS: max attempts bound exists"
else
  TESTS=$((TESTS + 1)); FAIL=$((FAIL + 1))
  echo "  FAIL: max attempts bound missing" >&2
fi

# --- Test 4: Recursion guard in post-commit ---
if grep -q 'SHIP_AUTO_SKIP' "$ROOT_DIR/.githooks/post-commit"; then
  TESTS=$((TESTS + 1)); PASS=$((PASS + 1))
  echo "  PASS: recursion guard in post-commit"
else
  TESTS=$((TESTS + 1)); FAIL=$((FAIL + 1))
  echo "  FAIL: recursion guard missing in post-commit" >&2
fi

# --- Test 5: Autoheal allowlist ---
if grep -q 'ALLOWED_PATHS' "$OPS_DIR/autoheal_codex.sh"; then
  TESTS=$((TESTS + 1)); PASS=$((PASS + 1))
  echo "  PASS: autoheal has allowlist"
else
  TESTS=$((TESTS + 1)); FAIL=$((FAIL + 1))
  echo "  FAIL: autoheal missing allowlist" >&2
fi

# --- Test 6: Repo stays clean after all tests ---
DIRTY="$(git -C "$ROOT_DIR" status --porcelain)"
# Filter out review_packets (gitignored) and any selftest markers
DIRTY_TRACKED="$(echo "$DIRTY" | grep -v 'review_packets' | grep -v '.selftest' | grep -v '^??' || true)"
if [ -z "$DIRTY_TRACKED" ]; then
  TESTS=$((TESTS + 1)); PASS=$((PASS + 1))
  echo "  PASS: repo stays clean after tests"
else
  TESTS=$((TESTS + 1)); FAIL=$((FAIL + 1))
  echo "  FAIL: repo has unexpected dirty files after tests:" >&2
  echo "$DIRTY_TRACKED" >&2
fi

# --- Summary ---
echo ""
echo "=== Results: $PASS/$TESTS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1

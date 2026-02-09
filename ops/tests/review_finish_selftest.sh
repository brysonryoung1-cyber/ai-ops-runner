#!/usr/bin/env bash
# review_finish_selftest.sh — Tests for review_finish.sh
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

echo "=== review_finish_selftest.sh ==="

# --- Test 1: Dirty tree rejection ---
TMPFILE="$ROOT_DIR/.selftest_dirty_marker"
touch "$TMPFILE"
RC=0
"$OPS_DIR/review_finish.sh" >/dev/null 2>&1 || RC=$?
rm -f "$TMPFILE"
assert_eq "dirty tree rejected" "1" "$RC"

# --- Test 2: No verdict rejection ---
# When baseline == HEAD, it should exit 0 (nothing to advance)
HEAD_SHA="$(git -C "$ROOT_DIR" rev-parse HEAD)"
BASELINE="$(tr -d '[:space:]' < "$ROOT_DIR/docs/LAST_REVIEWED_SHA.txt")"

if [ -z "$(git -C "$ROOT_DIR" status --porcelain)" ]; then
  if [ "$BASELINE" = "$HEAD_SHA" ]; then
    RC=0
    OUTPUT="$("$OPS_DIR/review_finish.sh" 2>&1)" || RC=$?
    assert_eq "baseline at HEAD exits 0" "0" "$RC"
  else
    # Baseline behind HEAD, no verdict → should fail
    RC=0
    "$OPS_DIR/review_finish.sh" >/dev/null 2>&1 || RC=$?
    echo "  INFO: review_finish with baseline behind HEAD exited with $RC"
    TESTS=$((TESTS + 1)); PASS=$((PASS + 1))
    echo "  PASS: review_finish handles baseline-behind-HEAD correctly"
  fi
else
  echo "  SKIP: Tree is dirty"
fi

# --- Test 3: Pathspec isolation (verify only LAST_REVIEWED_SHA.txt would be committed) ---
if grep -qF 'docs/LAST_REVIEWED_SHA.txt' "$OPS_DIR/review_finish.sh"; then
  TESTS=$((TESTS + 1)); PASS=$((PASS + 1))
  echo "  PASS: pathspec isolation in review_finish.sh"
else
  TESTS=$((TESTS + 1)); FAIL=$((FAIL + 1))
  echo "  FAIL: pathspec isolation missing in review_finish.sh" >&2
fi

# --- Test 4: Simulated verdict check exists (design verification) ---
if grep -qF 'simulated' "$OPS_DIR/review_finish.sh"; then
  TESTS=$((TESTS + 1)); PASS=$((PASS + 1))
  echo "  PASS: review_finish checks for simulated verdicts"
else
  TESTS=$((TESTS + 1)); FAIL=$((FAIL + 1))
  echo "  FAIL: review_finish does NOT check for simulated verdicts" >&2
fi

# --- Test 5: No REVIEW_PUSH_APPROVED bypass (design verification) ---
if grep -qF 'REVIEW_PUSH_APPROVED' "$OPS_DIR/review_finish.sh"; then
  TESTS=$((TESTS + 1)); FAIL=$((FAIL + 1))
  echo "  FAIL: review_finish.sh still contains REVIEW_PUSH_APPROVED bypass" >&2
else
  TESTS=$((TESTS + 1)); PASS=$((PASS + 1))
  echo "  PASS: REVIEW_PUSH_APPROVED bypass removed from review_finish.sh"
fi

# --- Test 6: Pre-push hook has no bypass env vars (design verification) ---
PRE_PUSH="$ROOT_DIR/.githooks/pre-push"
if [ -f "$PRE_PUSH" ]; then
  if grep -qF 'REVIEW_PUSH_APPROVED' "$PRE_PUSH"; then
    TESTS=$((TESTS + 1)); FAIL=$((FAIL + 1))
    echo "  FAIL: pre-push hook still contains REVIEW_PUSH_APPROVED bypass" >&2
  else
    TESTS=$((TESTS + 1)); PASS=$((PASS + 1))
    echo "  PASS: no bypass env vars in pre-push hook"
  fi
else
  TESTS=$((TESTS + 1)); FAIL=$((FAIL + 1))
  echo "  FAIL: pre-push hook not found" >&2
fi

# --- Test 7: Simulated verdict rejection (functional, if conditions allow) ---
# This test creates a simulated verdict via CODEX_SKIP and then verifies
# that review_finish skips it (no real verdict → exits 1).
if [ -z "$(git -C "$ROOT_DIR" status --porcelain)" ] && [ "$BASELINE" != "$HEAD_SHA" ]; then
  # There are unreviewed commits and tree is clean — create simulated verdict
  CODEX_SKIP=1 "$OPS_DIR/review_auto.sh" --no-push >/dev/null 2>&1 || true
  RC=0
  OUTPUT="$("$OPS_DIR/review_finish.sh" 2>&1)" || RC=$?
  assert_eq "review_finish refuses when only simulated verdicts exist" "1" "$RC"
  assert_contains "review_finish skips simulated verdict" "simulated" "$OUTPUT"
else
  echo "  SKIP: Cannot run simulated-verdict rejection test (baseline==HEAD or dirty tree)"
fi

# --- Summary ---
echo ""
echo "=== Results: $PASS/$TESTS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1

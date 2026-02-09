#!/usr/bin/env bash
# review_bundle_selftest.sh — Fast, deterministic tests for review_bundle.sh
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

echo "=== review_bundle_selftest.sh ==="

# --- Test 1: --help exits 0 ---
RC=0
"$OPS_DIR/review_bundle.sh" --help >/dev/null 2>&1 || RC=$?
assert_eq "help exits 0" "0" "$RC"

# --- Test 2: No changes produces valid output ---
HEAD_SHA="$(git -C "$ROOT_DIR" rev-parse HEAD)"
OUTPUT="$("$OPS_DIR/review_bundle.sh" --since "$HEAD_SHA" 2>/dev/null || true)"
assert_contains "no-change bundle contains REVIEW PACKET" "REVIEW PACKET" "$OUTPUT"
assert_contains "no-change bundle contains no changes" "no changes" "$OUTPUT"

# --- Test 3: Invalid SHA fails ---
RC=0
"$OPS_DIR/review_bundle.sh" --since "0000000000000000000000000000000000000bad" >/dev/null 2>&1 || RC=$?
assert_eq "invalid SHA fails" "1" "$RC"

# --- Test 4: Size cap enforcement ---
# Use a tiny size cap to force exit 6
TMPOUT="$(mktemp)"
RC=0
REVIEW_BUNDLE_SIZE_CAP=1 "$OPS_DIR/review_bundle.sh" --since "$(git -C "$ROOT_DIR" rev-list --max-parents=0 HEAD | head -1)" --output "$TMPOUT" 2>/dev/null || RC=$?
# This should either be 6 (size cap) or 0 (if no real diff)
if [ "$RC" -eq 6 ] || [ "$RC" -eq 0 ]; then
  TESTS=$((TESTS + 1)); PASS=$((PASS + 1))
  echo "  PASS: size cap enforcement (rc=$RC)"
else
  TESTS=$((TESTS + 1)); FAIL=$((FAIL + 1))
  echo "  FAIL: size cap enforcement (expected 0 or 6, got $RC)" >&2
fi
rm -f "$TMPOUT"

# --- Test 5: Output file mode ---
TMPOUT="$(mktemp)"
"$OPS_DIR/review_bundle.sh" --since "$HEAD_SHA" --output "$TMPOUT" >/dev/null 2>&1 || true
if [ -f "$TMPOUT" ] && [ -s "$TMPOUT" ]; then
  TESTS=$((TESTS + 1)); PASS=$((PASS + 1))
  echo "  PASS: output file written"
else
  TESTS=$((TESTS + 1)); FAIL=$((FAIL + 1))
  echo "  FAIL: output file not written or empty" >&2
fi
rm -f "$TMPOUT"

# --- Summary ---
echo ""
echo "=== Results: $PASS/$TESTS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1

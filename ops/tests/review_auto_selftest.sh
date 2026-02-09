#!/usr/bin/env bash
# review_auto_selftest.sh — Tests for review_auto.sh in CODEX_SKIP mode
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

echo "=== review_auto_selftest.sh ==="

# --- Test 1: --help exits 0 ---
RC=0
"$OPS_DIR/review_auto.sh" --help >/dev/null 2>&1 || RC=$?
assert_eq "help exits 0" "0" "$RC"

# --- Test 2: CODEX_SKIP mode produces APPROVED verdict ---
# Only run if tree is clean
if [ -z "$(git -C "$ROOT_DIR" status --porcelain)" ]; then
  RC=0
  OUTPUT="$(CODEX_SKIP=1 "$OPS_DIR/review_auto.sh" --no-push 2>&1)" || RC=$?
  assert_eq "CODEX_SKIP=1 review exits 0" "0" "$RC"
  assert_contains "CODEX_SKIP=1 shows APPROVED" "APPROVED" "$OUTPUT"

  # Verify verdict file was created
  LATEST_PACK="$(ls -td "$ROOT_DIR"/review_packets/*/ 2>/dev/null | head -1)"
  if [ -n "$LATEST_PACK" ] && [ -f "${LATEST_PACK}CODEX_VERDICT.json" ]; then
    TESTS=$((TESTS + 1)); PASS=$((PASS + 1))
    echo "  PASS: verdict JSON created"

    # Verify verdict validates
    VERDICT="$(python3 -c "
import json
with open('${LATEST_PACK}CODEX_VERDICT.json') as f:
    v = json.load(f)
required = {'verdict', 'blockers', 'non_blocking', 'tests_run'}
extra = set(v.keys()) - required
missing = required - set(v.keys())
if extra:
    print(f'EXTRA:{extra}')
elif missing:
    print(f'MISSING:{missing}')
else:
    print('VALID')
" 2>/dev/null)"
    assert_eq "verdict schema valid" "VALID" "$VERDICT"

    # Verify META.json created
    if [ -f "${LATEST_PACK}META.json" ]; then
      TESTS=$((TESTS + 1)); PASS=$((PASS + 1))
      echo "  PASS: META.json created"
    else
      TESTS=$((TESTS + 1)); FAIL=$((FAIL + 1))
      echo "  FAIL: META.json not created" >&2
    fi
  else
    TESTS=$((TESTS + 1)); FAIL=$((FAIL + 1))
    echo "  FAIL: verdict JSON not found" >&2
  fi
else
  echo "  SKIP: Tree is dirty, skipping review_auto test"
fi

# --- Test 3: Dirty tree rejection ---
TMPFILE="$ROOT_DIR/.selftest_dirty_marker"
touch "$TMPFILE"
RC=0
CODEX_SKIP=1 "$OPS_DIR/review_auto.sh" --no-push >/dev/null 2>&1 || RC=$?
rm -f "$TMPFILE"
assert_eq "dirty tree rejected" "1" "$RC"

# --- Cleanup ---
# Remove any selftest review packets
rm -rf "$ROOT_DIR"/review_packets/*selftest* 2>/dev/null || true

# --- Summary ---
echo ""
echo "=== Results: $PASS/$TESTS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1

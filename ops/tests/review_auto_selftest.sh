#!/usr/bin/env bash
# review_auto_selftest.sh — Tests for review_auto.sh in CODEX_SKIP mode
# Verifies that CODEX_SKIP verdicts are correctly marked as simulated
# and include proper meta structure.
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

# --- Test 2: CODEX_SKIP mode produces APPROVED verdict with correct meta ---
# Only run if tree is clean
if [ -z "$(git -C "$ROOT_DIR" status --porcelain)" ]; then
  RC=0
  OUTPUT="$(CODEX_SKIP=1 "$OPS_DIR/review_auto.sh" --no-push 2>&1)" || RC=$?
  assert_eq "CODEX_SKIP=1 review exits 0" "0" "$RC"
  assert_contains "CODEX_SKIP=1 shows APPROVED" "APPROVED" "$OUTPUT"
  assert_contains "CODEX_SKIP=1 shows SIMULATED banner" "SIMULATED VERDICT" "$OUTPUT"

  # Verify verdict file was created and has correct structure (use pack dir from this run's output)
  LATEST_PACK="$(echo "$OUTPUT" | sed -n 's/.*Pack dir: \([^[:space:]]*\).*/\1/p' | head -1)"
  if [ -z "$LATEST_PACK" ]; then
    LATEST_PACK="$(ls -td "$ROOT_DIR"/review_packets/*/ 2>/dev/null | head -1)"
  fi
  [ -n "$LATEST_PACK" ] && LATEST_PACK="${LATEST_PACK%/}/"
  if [ -n "$LATEST_PACK" ] && [ -f "${LATEST_PACK}CODEX_VERDICT.json" ]; then
    TESTS=$((TESTS + 1)); PASS=$((PASS + 1))
    echo "  PASS: verdict JSON created"

    # Validate verdict schema including meta
    VERDICT_CHECK="$(python3 - "${LATEST_PACK}CODEX_VERDICT.json" <<'PYEOF'
import json, sys

with open(sys.argv[1]) as f:
    v = json.load(f)

required_top = {"verdict", "blockers", "non_blocking", "tests_run", "meta"}
extra = set(v.keys()) - required_top
missing = required_top - set(v.keys())

if extra:
    print("EXTRA:%s" % extra)
elif missing:
    print("MISSING:%s" % missing)
elif not isinstance(v.get("meta"), dict):
    print("META_NOT_DICT")
else:
    meta = v["meta"]
    meta_req = {"since_sha", "to_sha", "generated_at", "review_mode", "simulated"}
    meta_missing = meta_req - set(meta.keys())
    if meta_missing:
        print("META_MISSING:%s" % meta_missing)
    elif meta.get("simulated") is not True:
        print("NOT_SIMULATED")
    elif meta.get("codex_cli") is not None:
        print("CODEX_CLI_NOT_NULL")
    elif meta.get("review_mode") not in ["bundle", "packet"]:
        print("BAD_REVIEW_MODE:%s" % meta.get("review_mode"))
    elif not meta.get("since_sha"):
        print("EMPTY_SINCE_SHA")
    elif not meta.get("to_sha"):
        print("EMPTY_TO_SHA")
    else:
        print("VALID")
PYEOF
)"
    assert_eq "verdict schema valid (with meta)" "VALID" "$VERDICT_CHECK"

    # Verify meta.simulated is explicitly true
    SIM_CHECK="$(python3 -c "
import json
with open('${LATEST_PACK}CODEX_VERDICT.json') as f:
    v = json.load(f)
print('TRUE' if v.get('meta', {}).get('simulated') is True else 'FALSE')
" 2>/dev/null)"
    assert_eq "meta.simulated is True for CODEX_SKIP" "TRUE" "$SIM_CHECK"

    # Verify codex_cli is null for CODEX_SKIP
    CLI_CHECK="$(python3 -c "
import json
with open('${LATEST_PACK}CODEX_VERDICT.json') as f:
    v = json.load(f)
print('NULL' if v.get('meta', {}).get('codex_cli') is None else 'NOT_NULL')
" 2>/dev/null)"
    assert_eq "meta.codex_cli is null for CODEX_SKIP" "NULL" "$CLI_CHECK"

    # Verify META.json created (logging artifact)
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

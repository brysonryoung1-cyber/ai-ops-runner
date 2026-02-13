#!/usr/bin/env bash
# openai_key_selftest.sh — Tests for the secure OpenAI key loading mechanism
#
# Tests:
#   1. Key from env var succeeds (exit 0, correct output)
#   2. Missing key everywhere → fail-closed (exit non-zero)
#   3. Key is NEVER printed to stderr
#   4. ensure_openai_key.sh exports the key when env var set
#   5. ensure_openai_key.sh fails closed when key unavailable
#   6. Python helper validates key format (rejects empty)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

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

assert_not_contains() {
  TESTS=$((TESTS + 1))
  local desc="$1" forbidden="$2" haystack="$3"
  if echo "$haystack" | grep -qF "$forbidden" 2>/dev/null; then
    echo "  FAIL: $desc (stderr contains the secret key)" >&2
    FAIL=$((FAIL + 1))
  else
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  fi
}

echo "=== openai_key_selftest.sh ==="

# Use a fake key for testing (never a real key)
FAKE_KEY="sk-selftest-FAKE-key-00000000000000000000000000"

# Null backend prevents the test from finding a real key in macOS Keychain.
# Without this, "missing key" tests pass in CI but fail on dev machines
# where a real key has been stored via `python3 ops/openai_key.py`.
NULL_BACKEND="keyring.backends.null.Keyring"

# --- Test 1: Key from env var → stdout, exit 0 ---
RC=0
STDOUT="$(OPENAI_API_KEY="$FAKE_KEY" python3 "$OPS_DIR/openai_key.py" 2>/dev/null)" || RC=$?
assert_eq "env var key: exit 0" "0" "$RC"
assert_eq "env var key: stdout matches" "$FAKE_KEY" "$STDOUT"

# --- Test 2: Key from env var → NEVER in stderr ---
RC=0
STDERR="$(OPENAI_API_KEY="$FAKE_KEY" python3 "$OPS_DIR/openai_key.py" 2>&1 >/dev/null)" || RC=$?
assert_not_contains "env var key: not in stderr" "$FAKE_KEY" "$STDERR"

# --- Test 3: No key anywhere → fail-closed (non-interactive, no TTY) ---
# Unset env var, disable keyring, use /dev/null as stdin to prevent TTY prompt
RC=0
STDERR="$(env -u OPENAI_API_KEY PYTHON_KEYRING_BACKEND="$NULL_BACKEND" python3 "$OPS_DIR/openai_key.py" </dev/null 2>&1 >/dev/null)" || RC=$?
assert_eq "missing key: non-zero exit" "1" "$RC"

# --- Test 4: Missing key stderr does NOT contain any actual key ---
# (Ensure error messages are safe to display)
assert_not_contains "missing key: no secret in stderr" "$FAKE_KEY" "$STDERR"

# --- Test 5: ensure_openai_key.sh exports key from env var ---
RC=0
RESULT="$(OPENAI_API_KEY="$FAKE_KEY" bash -c '
  source "'"$OPS_DIR"'/ensure_openai_key.sh"
  echo "$OPENAI_API_KEY"
' 2>/dev/null)" || RC=$?
assert_eq "ensure_openai_key.sh: exit 0 with env var" "0" "$RC"
assert_eq "ensure_openai_key.sh: key exported correctly" "$FAKE_KEY" "$RESULT"

# --- Test 6: ensure_openai_key.sh fails closed without key ---
RC=0
env -u OPENAI_API_KEY PYTHON_KEYRING_BACKEND="$NULL_BACKEND" bash -c '
  source "'"$OPS_DIR"'/ensure_openai_key.sh"
' </dev/null >/dev/null 2>&1 || RC=$?
assert_eq "ensure_openai_key.sh: fail-closed without key" "1" "$RC"

# --- Test 7: openai_key.py with empty OPENAI_API_KEY treats as missing ---
RC=0
OPENAI_API_KEY="" PYTHON_KEYRING_BACKEND="$NULL_BACKEND" python3 "$OPS_DIR/openai_key.py" </dev/null >/dev/null 2>&1 || RC=$?
assert_eq "empty env var: treated as missing (non-zero)" "1" "$RC"

# --- Test 8: openai_key.py with whitespace-only key treats as missing ---
RC=0
OPENAI_API_KEY="   " PYTHON_KEYRING_BACKEND="$NULL_BACKEND" python3 "$OPS_DIR/openai_key.py" </dev/null >/dev/null 2>&1 || RC=$?
assert_eq "whitespace-only env var: treated as missing" "1" "$RC"

# --- Test 9: review_auto.sh --help still works (no key required) ---
RC=0
env -u OPENAI_API_KEY "$OPS_DIR/review_auto.sh" --help >/dev/null 2>&1 || RC=$?
assert_eq "review_auto.sh --help works without key" "0" "$RC"

# --- Test 10: CODEX_SKIP mode does NOT require key ---
# (CODEX_SKIP review runs without Codex, so no key needed)
# Only test if tree is clean
if [ -z "$(git -C "$(cd "$OPS_DIR/.." && pwd)" status --porcelain)" ]; then
  RC=0
  env -u OPENAI_API_KEY CODEX_SKIP=1 "$OPS_DIR/review_auto.sh" --no-push >/dev/null 2>&1 || RC=$?
  assert_eq "CODEX_SKIP=1: works without OPENAI_API_KEY" "0" "$RC"
else
  echo "  SKIP: Tree is dirty, skipping CODEX_SKIP test"
fi

# --- Summary ---
echo ""
echo "=== Results: $PASS/$TESTS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1

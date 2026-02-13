#!/usr/bin/env bash
# openai_key_selftest.sh — Tests for the secure OpenAI key loading mechanism
#
# Tests:
#   1. --emit-env with env var succeeds (exit 0, correct export output)
#   2. Key is NEVER printed to stderr
#   3. Missing key everywhere → fail-closed (exit non-zero) via --emit-env
#   4. ensure_openai_key.sh exports the key when env var set
#   5. ensure_openai_key.sh fails closed when key unavailable
#   6. Empty/whitespace env var treated as missing
#   7. Status subcommand returns masked output
#   8. Status output does NOT contain full key
#   9. Default mode (no subcommand) shows masked status, never raw key
#   10. review_auto.sh --help works without key
#   11. CODEX_SKIP mode does NOT require key
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
    echo "  FAIL: $desc (output contains the secret key)" >&2
    FAIL=$((FAIL + 1))
  else
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  fi
}

assert_contains() {
  TESTS=$((TESTS + 1))
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF "$needle" 2>/dev/null; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc (output does not contain '$needle')" >&2
    FAIL=$((FAIL + 1))
  fi
}

echo "=== openai_key_selftest.sh ==="

# Use a fake key for testing (never a real key)
FAKE_KEY="sk-selftest-FAKE-key-00000000000000000000000000"

# Null backend prevents the test from finding a real key in macOS Keychain.
NULL_BACKEND="keyring.backends.null.Keyring"

# --- Test 1: --emit-env with env var → correct export output, exit 0 ---
RC=0
STDOUT="$(OPENAI_API_KEY="$FAKE_KEY" python3 "$OPS_DIR/openai_key.py" --emit-env 2>/dev/null)" || RC=$?
assert_eq "emit-env: exit 0" "0" "$RC"
# shlex.quote only adds quotes if the string contains special chars;
# our fake key is shell-safe so no quotes are added.
assert_contains "emit-env: output starts with export" "export OPENAI_API_KEY=" "$STDOUT"
assert_contains "emit-env: output contains key value" "$FAKE_KEY" "$STDOUT"

# --- Test 2: --emit-env key → NEVER in stderr ---
RC=0
STDERR="$(OPENAI_API_KEY="$FAKE_KEY" python3 "$OPS_DIR/openai_key.py" --emit-env 2>&1 >/dev/null)" || RC=$?
assert_not_contains "emit-env: key not in stderr" "$FAKE_KEY" "$STDERR"

# --- Test 3: No key anywhere → fail-closed via --emit-env ---
RC=0
STDERR="$(env -u OPENAI_API_KEY PYTHON_KEYRING_BACKEND="$NULL_BACKEND" python3 "$OPS_DIR/openai_key.py" --emit-env </dev/null 2>&1 >/dev/null)" || RC=$?
assert_eq "missing key --emit-env: non-zero exit" "1" "$RC"

# --- Test 4: Missing key stderr does NOT contain any actual key ---
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

# --- Test 7: Empty OPENAI_API_KEY treated as missing ---
RC=0
OPENAI_API_KEY="" PYTHON_KEYRING_BACKEND="$NULL_BACKEND" python3 "$OPS_DIR/openai_key.py" --emit-env </dev/null >/dev/null 2>&1 || RC=$?
assert_eq "empty env var: treated as missing (non-zero)" "1" "$RC"

# --- Test 8: Whitespace-only key treated as missing ---
RC=0
OPENAI_API_KEY="   " PYTHON_KEYRING_BACKEND="$NULL_BACKEND" python3 "$OPS_DIR/openai_key.py" --emit-env </dev/null >/dev/null 2>&1 || RC=$?
assert_eq "whitespace-only env var: treated as missing" "1" "$RC"

# --- Test 9: Status subcommand shows masked output ---
RC=0
STDOUT="$(OPENAI_API_KEY="$FAKE_KEY" python3 "$OPS_DIR/openai_key.py" status 2>/dev/null)" || RC=$?
assert_eq "status: exit 0" "0" "$RC"
assert_contains "status: shows 'OpenAI API key:'" "OpenAI API key:" "$STDOUT"
assert_not_contains "status: no raw key in output" "$FAKE_KEY" "$STDOUT"

# --- Test 10: Default mode (no subcommand) → status, never raw key ---
RC=0
STDOUT="$(OPENAI_API_KEY="$FAKE_KEY" python3 "$OPS_DIR/openai_key.py" 2>/dev/null)" || RC=$?
assert_eq "default mode: exit 0" "0" "$RC"
assert_not_contains "default mode: no raw key in stdout" "$FAKE_KEY" "$STDOUT"
assert_contains "default mode: shows masked" "…" "$STDOUT"

# --- Test 11: review_auto.sh --help still works (no key required) ---
RC=0
env -u OPENAI_API_KEY "$OPS_DIR/review_auto.sh" --help >/dev/null 2>&1 || RC=$?
assert_eq "review_auto.sh --help works without key" "0" "$RC"

# --- Test 12: CODEX_SKIP mode does NOT require key ---
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

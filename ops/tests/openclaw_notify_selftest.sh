#!/usr/bin/env bash
# openclaw_notify_selftest.sh — Hermetic tests for openclaw_notify.sh
#
# Tests notification formatting, rate limiting, secret loading, and dry-run mode.
# NO real network calls. NO real secrets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
NOTIFY="$OPS_DIR/openclaw_notify.sh"

TESTS_PASSED=0
TESTS_FAILED=0
TESTS_RUN=0

pass() { TESTS_RUN=$((TESTS_RUN + 1)); TESTS_PASSED=$((TESTS_PASSED + 1)); echo "  PASS [$TESTS_RUN]: $1"; }
fail() { TESTS_RUN=$((TESTS_RUN + 1)); TESTS_FAILED=$((TESTS_FAILED + 1)); echo "  FAIL [$TESTS_RUN]: $1" >&2; }

echo "=== openclaw_notify_selftest.sh ==="

# --- Setup test environment ---
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT

# Create a mock curl that records call args AND stdin
MOCK_BIN="$TEST_ROOT/bin"
mkdir -p "$MOCK_BIN"
cat > "$MOCK_BIN/curl" <<'MOCKEOF'
#!/usr/bin/env bash
# Mock curl — records call args + stdin body and succeeds
LOG="${OPENCLAW_NOTIFY_TEST_CURL_LOG:-/dev/null}"
echo "ARGS: $@" >> "$LOG"
# Also capture stdin (for -d @- payloads)
if [[ "$*" == *"@-"* ]]; then
  STDIN_DATA="$(cat)"
  echo "STDIN: $STDIN_DATA" >> "$LOG"
fi
exit 0
MOCKEOF
chmod +x "$MOCK_BIN/curl"

export OPENCLAW_NOTIFY_RATE_DIR="$TEST_ROOT/ratelimit"

# --- Test 1: Dry-run mode ---
OUTPUT="$(PUSHOVER_APP_TOKEN=test_token PUSHOVER_USER_KEY=test_user \
  bash "$NOTIFY" --dry-run "Test message" 2>&1)"
if echo "$OUTPUT" | grep -q "DRY_RUN"; then
  pass "Dry-run mode outputs DRY_RUN"
else
  fail "Dry-run mode missing DRY_RUN output"
fi

# --- Test 2: Dry-run includes title ---
OUTPUT="$(PUSHOVER_APP_TOKEN=test PUSHOVER_USER_KEY=test \
  bash "$NOTIFY" --dry-run --title "TestTitle" "msg" 2>&1)"
if echo "$OUTPUT" | grep -q "TestTitle"; then
  pass "Dry-run shows title"
else
  fail "Dry-run missing title"
fi

# --- Test 3: Dry-run includes priority ---
OUTPUT="$(PUSHOVER_APP_TOKEN=test PUSHOVER_USER_KEY=test \
  bash "$NOTIFY" --dry-run --priority high "msg" 2>&1)"
if echo "$OUTPUT" | grep -q "high"; then
  pass "Dry-run shows priority"
else
  fail "Dry-run missing priority"
fi

# --- Test 4: Missing message exits non-zero ---
RC=0
bash "$NOTIFY" 2>/dev/null || RC=$?
if [ "$RC" -ne 0 ]; then
  pass "Missing message exits non-zero"
else
  fail "Missing message should exit non-zero"
fi

# --- Test 5: Missing secrets exits non-zero ---
RC=0
unset PUSHOVER_APP_TOKEN PUSHOVER_USER_KEY 2>/dev/null || true
bash "$NOTIFY" "test" 2>/dev/null || RC=$?
if [ "$RC" -ne 0 ]; then
  pass "Missing secrets exits non-zero"
else
  fail "Missing secrets should exit non-zero"
fi

# --- Test 6: Rate limiting — first call sends ---
mkdir -p "$OPENCLAW_NOTIFY_RATE_DIR"
OUTPUT="$(PUSHOVER_APP_TOKEN=test PUSHOVER_USER_KEY=test \
  bash "$NOTIFY" --dry-run --rate-key "test_check_1" "first call" 2>&1)"
if echo "$OUTPUT" | grep -q "DRY_RUN"; then
  pass "Rate limit: first call sends"
else
  fail "Rate limit: first call should send"
fi

# --- Test 7: Rate limiting — second call within window is suppressed ---
# Write a recent timestamp to the rate limit file (simulates a prior successful send)
RATE_HASH="$(echo -n "test_check_2" | shasum -a 256 2>/dev/null | cut -d' ' -f1 || echo -n "test_check_2" | sha256sum 2>/dev/null | cut -d' ' -f1)"
mkdir -p "$OPENCLAW_NOTIFY_RATE_DIR"
date +%s > "$OPENCLAW_NOTIFY_RATE_DIR/$RATE_HASH"

OUTPUT="$(PUSHOVER_APP_TOKEN=test PUSHOVER_USER_KEY=test \
  OPENCLAW_NOTIFY_RATE_LIMIT_SEC=9999 \
  bash "$NOTIFY" --dry-run --rate-key "test_check_2" "second call" 2>&1)"
if echo "$OUTPUT" | grep -q "RATE_LIMITED"; then
  pass "Rate limit: repeat suppressed within window"
else
  fail "Rate limit: repeat should be suppressed"
fi

# --- Test 8: Rate limiting — expired window allows send ---
RATE_HASH3="$(echo -n "test_check_3" | shasum -a 256 2>/dev/null | cut -d' ' -f1 || echo -n "test_check_3" | sha256sum 2>/dev/null | cut -d' ' -f1)"
echo "0" > "$OPENCLAW_NOTIFY_RATE_DIR/$RATE_HASH3"

OUTPUT="$(PUSHOVER_APP_TOKEN=test PUSHOVER_USER_KEY=test \
  OPENCLAW_NOTIFY_RATE_LIMIT_SEC=1 \
  bash "$NOTIFY" --dry-run --rate-key "test_check_3" "expired window" 2>&1)"
if echo "$OUTPUT" | grep -q "DRY_RUN"; then
  pass "Rate limit: expired window allows send"
else
  fail "Rate limit: expired window should allow send"
fi

# --- Test 9: Help flag ---
OUTPUT="$(bash "$NOTIFY" --help 2>&1)"
if echo "$OUTPUT" | grep -q "Usage"; then
  pass "Help flag shows usage"
else
  fail "Help flag missing usage"
fi

# --- Test 10: Unknown flag exits non-zero ---
RC=0
bash "$NOTIFY" --unknown-flag 2>/dev/null || RC=$?
if [ "$RC" -ne 0 ]; then
  pass "Unknown flag exits non-zero"
else
  fail "Unknown flag should exit non-zero"
fi

# --- Test 11: Mock curl — actual send path ---
CURL_LOG="$TEST_ROOT/curl_calls.log"
rm -f "$CURL_LOG"
OUTPUT="$(PUSHOVER_APP_TOKEN=mock_app PUSHOVER_USER_KEY=mock_user \
  OPENCLAW_NOTIFY_CURL_CMD="$MOCK_BIN/curl" \
  OPENCLAW_NOTIFY_TEST_CURL_LOG="$CURL_LOG" \
  bash "$NOTIFY" --title "MockTest" "mock message" 2>&1)"
# Secrets are now in stdin (JSON body), NOT in argv — verify via STDIN log
if [ -f "$CURL_LOG" ] && grep -q "mock_app" "$CURL_LOG"; then
  pass "Mock curl received app token via JSON body (stdin)"
else
  fail "Mock curl did not receive app token"
fi

# --- Test 12: Mock curl — includes message ---
if [ -f "$CURL_LOG" ] && grep -q "mock message" "$CURL_LOG"; then
  pass "Mock curl includes message in JSON body"
else
  fail "Mock curl missing message"
fi

# --- Test 13: Secrets never printed in dry-run ---
OUTPUT="$(PUSHOVER_APP_TOKEN=SECRETTOKEN123 PUSHOVER_USER_KEY=SECRETUSER456 \
  bash "$NOTIFY" --dry-run "test" 2>&1)"
if echo "$OUTPUT" | grep -q "SECRETTOKEN123"; then
  fail "Secret token printed in dry-run output"
elif echo "$OUTPUT" | grep -q "SECRETUSER456"; then
  fail "Secret user key printed in dry-run output"
else
  pass "Secrets not printed in dry-run"
fi

# --- Test 14: Test mode without secrets ---
RC=0
unset PUSHOVER_APP_TOKEN PUSHOVER_USER_KEY 2>/dev/null || true
bash "$NOTIFY" --test 2>/dev/null || RC=$?
if [ "$RC" -ne 0 ]; then
  pass "Test mode fails without secrets"
else
  fail "Test mode should fail without secrets"
fi

# --- Summary ---
echo ""
echo "=== Notify Selftest: $TESTS_PASSED/$TESTS_RUN passed ==="
if [ "$TESTS_FAILED" -gt 0 ]; then
  echo "FAIL: $TESTS_FAILED test(s) failed" >&2
  exit 1
fi
echo "All tests passed."
exit 0

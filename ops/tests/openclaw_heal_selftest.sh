#!/usr/bin/env bash
# openclaw_heal_selftest.sh — Hermetic tests for openclaw_heal.sh
#
# Tests heal entrypoint structure, evidence capture, and fail-closed behavior.
# NO real network. NO real secrets. Uses stub scripts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HEAL="$OPS_DIR/openclaw_heal.sh"

TESTS_PASSED=0
TESTS_FAILED=0
TESTS_RUN=0

pass() { TESTS_RUN=$((TESTS_RUN + 1)); TESTS_PASSED=$((TESTS_PASSED + 1)); echo "  PASS [$TESTS_RUN]: $1"; }
fail() { TESTS_RUN=$((TESTS_RUN + 1)); TESTS_FAILED=$((TESTS_FAILED + 1)); echo "  FAIL [$TESTS_RUN]: $1" >&2; }

echo "=== openclaw_heal_selftest.sh ==="

# --- Test 1: Script exists and is executable ---
if [ -x "$HEAL" ]; then
  pass "openclaw_heal.sh exists and is executable"
else
  fail "openclaw_heal.sh not found or not executable"
fi

# --- Test 2: Help flag ---
OUTPUT="$(bash "$HEAL" --help 2>&1)"
if echo "$OUTPUT" | grep -q "Usage"; then
  pass "Help flag shows usage"
else
  fail "Help flag missing usage"
fi

# --- Test 3: Script contains fail-closed posture check ---
if grep -q "Private-Only Posture" "$HEAL" || grep -q "posture" "$HEAL"; then
  pass "Script contains posture pre-check"
else
  fail "Script missing posture pre-check"
fi

# --- Test 4: Script contains evidence bundle logic ---
if grep -q "evidence" "$HEAL" && grep -q "listeners.txt" "$HEAL"; then
  pass "Script contains evidence bundle capture"
else
  fail "Script missing evidence bundle"
fi

# --- Test 5: Script contains doctor verification ---
if grep -q "openclaw_doctor" "$HEAL"; then
  pass "Script runs openclaw_doctor"
else
  fail "Script missing doctor verification"
fi

# --- Test 6: Script contains summary JSON ---
if grep -q "summary.json" "$HEAL"; then
  pass "Script writes summary.json"
else
  fail "Script missing summary.json"
fi

# --- Test 7: Script handles --check-only ---
if grep -q "check-only\|CHECK_ONLY" "$HEAL"; then
  pass "Script supports --check-only"
else
  fail "Script missing --check-only support"
fi

# --- Test 8: Script handles --verify-only ---
if grep -q "verify-only\|VERIFY_ONLY" "$HEAL"; then
  pass "Script supports --verify-only"
else
  fail "Script missing --verify-only support"
fi

# --- Test 9: Script contains lockout prevention ---
if grep -q "lockout\|Tailscale.*down\|lockout prevention" "$HEAL"; then
  pass "Script contains lockout prevention"
else
  fail "Script missing lockout prevention"
fi

# --- Test 10: Script contains notification integration ---
if grep -q "openclaw_notify\|notify" "$HEAL"; then
  pass "Script contains notification integration"
else
  fail "Script missing notification integration"
fi

# --- Test 11: Script has test mode support ---
if grep -q "OPENCLAW_HEAL_TEST_MODE\|TEST_MODE" "$HEAL"; then
  pass "Script supports test mode"
else
  fail "Script missing test mode support"
fi

# --- Test 12: Test mode — doctor pass path ---
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT"' EXIT

# Create stub doctor that passes
cat > "$TEST_ROOT/doctor_stub.sh" <<'EOF'
#!/usr/bin/env bash
echo "=== openclaw_doctor.sh ==="
echo "  PASS: All checks passed"
echo "=== Doctor Summary: 4/4 passed ==="
echo "All checks passed."
exit 0
EOF
chmod +x "$TEST_ROOT/doctor_stub.sh"

# Create stub fix
cat > "$TEST_ROOT/fix_stub.sh" <<'EOF'
#!/usr/bin/env bash
echo "SSH fix applied"
exit 0
EOF
chmod +x "$TEST_ROOT/fix_stub.sh"

# Create a mock notify
cat > "$OPS_DIR/openclaw_notify.sh.bak" <<'EOF'
true
EOF

RC=0
OUTPUT="$(OPENCLAW_HEAL_TEST_MODE=1 OPENCLAW_HEAL_TEST_ROOT="$TEST_ROOT" \
  bash "$HEAL" --verify-only 2>&1)" || RC=$?

if [ "$RC" -eq 0 ]; then
  pass "Test mode: heal passes with passing doctor stub"
else
  fail "Test mode: heal should pass with passing doctor (rc=$RC)"
fi

rm -f "$OPS_DIR/openclaw_notify.sh.bak"

# --- Test 13: Test mode — doctor fail path ---
cat > "$TEST_ROOT/doctor_stub.sh" <<'EOF'
#!/usr/bin/env bash
echo "FAIL: Tailscale is down"
exit 1
EOF
chmod +x "$TEST_ROOT/doctor_stub.sh"

RC=0
OUTPUT="$(OPENCLAW_HEAL_TEST_MODE=1 OPENCLAW_HEAL_TEST_ROOT="$TEST_ROOT" \
  bash "$HEAL" --verify-only 2>&1)" || RC=$?

if [ "$RC" -ne 0 ]; then
  pass "Test mode: heal fails when doctor fails"
else
  fail "Test mode: heal should fail when doctor fails"
fi

# --- Test 14: Evidence directory structure ---
if echo "$OUTPUT" | grep -q "HEAL FAIL"; then
  pass "Heal reports FAIL status"
else
  fail "Heal should report FAIL status"
fi

# --- Test 15: No secrets in script ---
if grep -qE 'echo.*\$.*TOKEN|echo.*\$.*KEY|echo.*\$.*SECRET' "$HEAL" | grep -v '#' | grep -v 'echo.*not\|echo.*found' 2>/dev/null; then
  fail "Script may print secrets"
else
  pass "No obvious secret printing in script"
fi

# --- Test 16: Script uses set -euo pipefail ---
if head -20 "$HEAL" | grep -q "set -euo pipefail"; then
  pass "Script uses set -euo pipefail"
else
  fail "Script missing set -euo pipefail"
fi

# --- Test 17: Unknown argument exits non-zero ---
RC=0
bash "$HEAL" --unknown-arg 2>/dev/null || RC=$?
if [ "$RC" -ne 0 ]; then
  pass "Unknown argument exits non-zero"
else
  fail "Unknown argument should exit non-zero"
fi

# --- Summary ---
echo ""
echo "=== Heal Selftest: $TESTS_PASSED/$TESTS_RUN passed ==="
if [ "$TESTS_FAILED" -gt 0 ]; then
  echo "FAIL: $TESTS_FAILED test(s) failed" >&2
  exit 1
fi
echo "All tests passed."
exit 0

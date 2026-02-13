#!/usr/bin/env bash
# openclaw_guard_selftest.sh — Hermetic tests for openclaw_guard.sh
#
# Tests guard logic with stubs:
#   - Doctor pass → log PASS, exit 0
#   - Doctor fail + no Tailscale → skip remediation (safety), exit 1
#   - Doctor fail + Tailscale up + sshd public → remediate → re-run doctor
#   - Doctor fail + Tailscale up + sshd NOT public → skip remediation
#   - Remediation fails → exit nonzero
#   - Log file written correctly
#
# No root required, no real system changes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
GUARD_SCRIPT="$ROOT_DIR/ops/openclaw_guard.sh"

ERRORS=0
PASS_COUNT=0

pass() { echo "  [PASS] $1"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail() { echo "  [FAIL] $1" >&2; ERRORS=$((ERRORS + 1)); }

echo "=== openclaw_guard Selftest ==="
echo ""

# ---------------------------------------------------------------------------
# Setup: create temp dirs
# ---------------------------------------------------------------------------
TEST_ROOT="$(mktemp -d)"
STUB_BIN="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT" "$STUB_BIN"' EXIT

# Stub: tailscale (returns tailnet IP by default)
cat > "$STUB_BIN/tailscale" << 'STUBEOF'
#!/bin/sh
if [ "${1:-}" = "ip" ] && [ "${2:-}" = "-4" ]; then
  echo "100.100.50.1"
elif [ "${1:-}" = "status" ]; then
  exit 0
fi
STUBEOF
chmod +x "$STUB_BIN/tailscale"

# Stub: ss (returns sshd on public by default — tests override)
cat > "$STUB_BIN/ss" << 'STUBEOF'
#!/bin/sh
echo 'LISTEN  0  128  0.0.0.0:22  0.0.0.0:*  users:(("sshd",pid=999,fd=3))'
STUBEOF
chmod +x "$STUB_BIN/ss"

# Stub: sudo (just runs the command without privilege escalation)
cat > "$STUB_BIN/sudo" << 'STUBEOF'
#!/bin/sh
exec "$@"
STUBEOF
chmod +x "$STUB_BIN/sudo"

# Stub: hostname
cat > "$STUB_BIN/hostname" << 'STUBEOF'
#!/bin/sh
echo "test-host"
STUBEOF
chmod +x "$STUB_BIN/hostname"

# ---------------------------------------------------------------------------
# Helper: create doctor and fix stubs in test root
# ---------------------------------------------------------------------------
setup_stubs() {
  local doctor_rc="${1:-0}"
  local fix_rc="${2:-0}"
  local doctor_post_rc="${3:-0}"

  cat > "$TEST_ROOT/doctor_stub.sh" << STUBEOF
#!/bin/sh
echo "=== openclaw_doctor.sh ==="
echo "  Doctor stub: rc=$doctor_rc"
exit $doctor_rc
STUBEOF
  chmod +x "$TEST_ROOT/doctor_stub.sh"

  cat > "$TEST_ROOT/fix_stub.sh" << STUBEOF
#!/bin/sh
echo "=== openclaw_fix_ssh_tailscale_only.sh ==="
echo "  Fix stub: rc=$fix_rc"
exit $fix_rc
STUBEOF
  chmod +x "$TEST_ROOT/fix_stub.sh"

  cat > "$TEST_ROOT/doctor_stub_post.sh" << STUBEOF
#!/bin/sh
echo "=== openclaw_doctor.sh (post-remediation) ==="
echo "  Doctor post stub: rc=$doctor_post_rc"
exit $doctor_post_rc
STUBEOF
  chmod +x "$TEST_ROOT/doctor_stub_post.sh"
}

run_guard() {
  PATH="$STUB_BIN:$PATH" \
    OPENCLAW_GUARD_TEST_ROOT="$TEST_ROOT" \
    OPENCLAW_GUARD_TEST_MODE=1 \
    bash "$GUARD_SCRIPT" 2>&1
}

# ---------------------------------------------------------------------------
# Test 1: Doctor passes → PASS, exit 0
# ---------------------------------------------------------------------------
echo "--- Test 1: Doctor passes → PASS ---"
rm -f "$TEST_ROOT/openclaw_guard.log"
setup_stubs 0 0 0

RC=0
OUTPUT="$(run_guard)" || RC=$?
if [ "$RC" -eq 0 ]; then
  pass "Guard exits 0 when doctor passes"
else
  fail "Guard should exit 0 when doctor passes (got $RC)"
fi
if grep -q "PASS" "$TEST_ROOT/openclaw_guard.log"; then
  pass "Log file contains PASS"
else
  fail "Log file missing PASS entry"
fi

# ---------------------------------------------------------------------------
# Test 2: Doctor fails + no Tailscale → skip remediation, exit 1
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 2: Doctor fails + no Tailscale → skip remediation ---"
rm -f "$TEST_ROOT/openclaw_guard.log"
setup_stubs 1 0 0

# Create tailscale stub that returns nothing (Tailscale down)
cat > "$STUB_BIN/tailscale" << 'STUBEOF'
#!/bin/sh
if [ "${1:-}" = "ip" ] && [ "${2:-}" = "-4" ]; then
  echo "" >&2
  exit 1
fi
STUBEOF
chmod +x "$STUB_BIN/tailscale"

RC=0
OUTPUT="$(run_guard)" || RC=$?
if [ "$RC" -ne 0 ]; then
  pass "Guard exits nonzero when Tailscale is down"
else
  fail "Guard should exit nonzero when Tailscale is down"
fi
if grep -q "SKIP REMEDIATION" "$TEST_ROOT/openclaw_guard.log"; then
  pass "Log says SKIP REMEDIATION when Tailscale down"
else
  fail "Log missing SKIP REMEDIATION message"
fi
# CRITICAL: verify fix script was NOT called
if grep -q "REMEDIATING" "$TEST_ROOT/openclaw_guard.log"; then
  fail "CRITICAL: Guard attempted remediation WITHOUT Tailscale!"
else
  pass "Guard did NOT attempt remediation without Tailscale (safe)"
fi

# Restore tailscale stub
cat > "$STUB_BIN/tailscale" << 'STUBEOF'
#!/bin/sh
if [ "${1:-}" = "ip" ] && [ "${2:-}" = "-4" ]; then
  echo "100.100.50.1"
elif [ "${1:-}" = "status" ]; then
  exit 0
fi
STUBEOF
chmod +x "$STUB_BIN/tailscale"

# ---------------------------------------------------------------------------
# Test 3: Doctor fails + Tailscale up + sshd public → remediate → pass
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 3: Doctor fails + Tailscale up + sshd public → remediate ---"
rm -f "$TEST_ROOT/openclaw_guard.log"
setup_stubs 1 0 0  # doctor fails, fix succeeds, post-doctor passes

# ss returns sshd on public (default stub)
cat > "$STUB_BIN/ss" << 'STUBEOF'
#!/bin/sh
echo 'LISTEN  0  128  0.0.0.0:22  0.0.0.0:*  users:(("sshd",pid=999,fd=3))'
STUBEOF
chmod +x "$STUB_BIN/ss"

RC=0
OUTPUT="$(run_guard)" || RC=$?
if [ "$RC" -eq 0 ]; then
  pass "Guard exits 0 after successful remediation"
else
  fail "Guard should exit 0 after successful remediation (got $RC)"
fi
if grep -q "REMEDIATING" "$TEST_ROOT/openclaw_guard.log"; then
  pass "Log shows remediation was attempted"
else
  fail "Log missing REMEDIATING message"
fi
if grep -q "PASS after remediation" "$TEST_ROOT/openclaw_guard.log"; then
  pass "Log shows PASS after remediation"
else
  fail "Log missing 'PASS after remediation'"
fi

# ---------------------------------------------------------------------------
# Test 4: Doctor fails + Tailscale up + sshd NOT public → skip SSH fix
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 4: Doctor fails + sshd on tailnet (not public) → skip SSH fix ---"
rm -f "$TEST_ROOT/openclaw_guard.log"
setup_stubs 1 0 0

# ss returns sshd on tailnet only (NOT public)
cat > "$STUB_BIN/ss" << 'STUBEOF'
#!/bin/sh
echo 'LISTEN  0  128  100.100.50.1:22  0.0.0.0:*  users:(("sshd",pid=999,fd=3))'
STUBEOF
chmod +x "$STUB_BIN/ss"

RC=0
OUTPUT="$(run_guard)" || RC=$?
if [ "$RC" -ne 0 ]; then
  pass "Guard exits nonzero when sshd is not public (different failure cause)"
else
  fail "Guard should exit nonzero when doctor fails for non-SSH reason"
fi
if grep -q "SKIP SSH REMEDIATION" "$TEST_ROOT/openclaw_guard.log"; then
  pass "Log says SKIP SSH REMEDIATION when sshd not public"
else
  fail "Log missing SKIP SSH REMEDIATION message"
fi
if grep -q "REMEDIATING" "$TEST_ROOT/openclaw_guard.log"; then
  fail "Guard should NOT remediate when sshd is not public"
else
  pass "Guard did NOT attempt SSH remediation (correct)"
fi

# Restore public ss stub for remaining tests
cat > "$STUB_BIN/ss" << 'STUBEOF'
#!/bin/sh
echo 'LISTEN  0  128  0.0.0.0:22  0.0.0.0:*  users:(("sshd",pid=999,fd=3))'
STUBEOF
chmod +x "$STUB_BIN/ss"

# ---------------------------------------------------------------------------
# Test 5: Remediation fails → exit nonzero
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 5: Remediation script fails → exit nonzero ---"
rm -f "$TEST_ROOT/openclaw_guard.log"
setup_stubs 1 1 0  # doctor fails, fix fails

RC=0
OUTPUT="$(run_guard)" || RC=$?
if [ "$RC" -ne 0 ]; then
  pass "Guard exits nonzero when remediation fails"
else
  fail "Guard should exit nonzero when remediation fails"
fi
if grep -q "REMEDIATION FAILED" "$TEST_ROOT/openclaw_guard.log"; then
  pass "Log shows REMEDIATION FAILED"
else
  fail "Log missing REMEDIATION FAILED"
fi

# ---------------------------------------------------------------------------
# Test 6: Remediation succeeds but post-doctor still fails → exit nonzero
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 6: Post-remediation doctor still fails → exit nonzero ---"
rm -f "$TEST_ROOT/openclaw_guard.log"
setup_stubs 1 0 1  # doctor fails, fix succeeds, post-doctor fails

RC=0
OUTPUT="$(run_guard)" || RC=$?
if [ "$RC" -ne 0 ]; then
  pass "Guard exits nonzero when post-remediation doctor fails"
else
  fail "Guard should exit nonzero when post-remediation doctor still fails"
fi
if grep -q "STILL FAILING" "$TEST_ROOT/openclaw_guard.log"; then
  pass "Log shows STILL FAILING"
else
  fail "Log missing STILL FAILING"
fi

# ---------------------------------------------------------------------------
# Test 7: Log file is append-mode (multiple runs)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 7: Log file append mode ---"
rm -f "$TEST_ROOT/openclaw_guard.log"
setup_stubs 0 0 0

run_guard >/dev/null 2>&1 || true
run_guard >/dev/null 2>&1 || true

LINE_COUNT="$(grep -c 'guard run:' "$TEST_ROOT/openclaw_guard.log" || echo 0)"
if [ "$LINE_COUNT" -ge 2 ]; then
  pass "Log file has entries from multiple runs ($LINE_COUNT entries)"
else
  fail "Log file should have multiple entries (got $LINE_COUNT)"
fi

# ---------------------------------------------------------------------------
# Test 8: IPv6 [::]:22 detected as public
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 8: IPv6 [::]:22 detected as public ---"
rm -f "$TEST_ROOT/openclaw_guard.log"
setup_stubs 1 0 0

cat > "$STUB_BIN/ss" << 'STUBEOF'
#!/bin/sh
echo 'LISTEN  0  128  [::]:22  [::]:*  users:(("sshd",pid=999,fd=4))'
STUBEOF
chmod +x "$STUB_BIN/ss"

RC=0
OUTPUT="$(run_guard)" || RC=$?
if [ "$RC" -eq 0 ]; then
  pass "Guard remediates [::]:22 (exits 0 after fix)"
else
  # Acceptable — the key test is that it attempted remediation
  if grep -q "REMEDIATING" "$TEST_ROOT/openclaw_guard.log"; then
    pass "Guard attempted remediation for [::]:22"
  else
    fail "Guard did not detect [::]:22 as public"
  fi
fi

# Restore standard ss stub
cat > "$STUB_BIN/ss" << 'STUBEOF'
#!/bin/sh
echo 'LISTEN  0  128  0.0.0.0:22  0.0.0.0:*  users:(("sshd",pid=999,fd=3))'
STUBEOF
chmod +x "$STUB_BIN/ss"

# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------
echo ""
echo "--- Static checks ---"

if grep -q 'set -euo pipefail' "$GUARD_SCRIPT"; then
  pass "Guard uses set -euo pipefail"
else
  fail "Guard missing set -euo pipefail"
fi

if grep -q 'OPENCLAW_GUARD_TEST_ROOT' "$GUARD_SCRIPT"; then
  pass "Guard supports test mode (OPENCLAW_GUARD_TEST_ROOT)"
else
  fail "Guard missing test mode support"
fi

if grep -q 'OPENCLAW_GUARD_TEST_MODE' "$GUARD_SCRIPT"; then
  pass "Guard supports OPENCLAW_GUARD_TEST_MODE"
else
  fail "Guard missing OPENCLAW_GUARD_TEST_MODE"
fi

if grep -q '/var/log/openclaw_guard.log' "$GUARD_SCRIPT"; then
  pass "Guard writes to /var/log/openclaw_guard.log"
else
  fail "Guard missing /var/log/openclaw_guard.log"
fi

if grep -q 'openclaw_doctor\.sh' "$GUARD_SCRIPT"; then
  pass "Guard runs openclaw_doctor.sh"
else
  fail "Guard missing doctor invocation"
fi

if grep -q 'openclaw_fix_ssh_tailscale_only\.sh' "$GUARD_SCRIPT"; then
  pass "Guard runs openclaw_fix_ssh_tailscale_only.sh for remediation"
else
  fail "Guard missing fix script invocation"
fi

if grep -q 'tailscale ip -4' "$GUARD_SCRIPT"; then
  pass "Guard checks tailscale ip -4 before remediation"
else
  fail "Guard missing tailscale ip -4 check"
fi

if grep -q 'SKIP REMEDIATION' "$GUARD_SCRIPT"; then
  pass "Guard has SKIP REMEDIATION path"
else
  fail "Guard missing SKIP REMEDIATION"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Summary: $PASS_COUNT passed, $ERRORS failed ==="
if [ "$ERRORS" -gt 0 ]; then
  echo "  $ERRORS error(s) found." >&2
  exit 1
fi
echo "  All guard tests passed!"
exit 0

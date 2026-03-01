#!/usr/bin/env bash
# ssh_tailscale_only_selftest.sh — Lightweight CI/mock-safe test for SSH tailscale-only fix.
# Validates script syntax, guardrails (exit non-zero if tailscale missing), and
# deploy_pipeline.sh integration (step 2e present).
# No real sshd edits. Safe to run without root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIX_SCRIPT="$ROOT_DIR/ops/openclaw_fix_ssh_tailscale_only.sh"
PIPELINE="$ROOT_DIR/ops/deploy_pipeline.sh"
PLAYBOOK="$ROOT_DIR/ops/playbooks/recover_infra_verify.sh"

ERRORS=0
PASS_COUNT=0

pass() { echo "  [PASS] $1"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail() { echo "  [FAIL] $1" >&2; ERRORS=$((ERRORS + 1)); }

echo "=== ssh_tailscale_only Selftest ==="
echo ""

# --- Test 1: Fix script exists and has valid bash syntax ---
echo "--- Test 1: Script syntax ---"
if [ -f "$FIX_SCRIPT" ]; then
  pass "Fix script exists"
else
  fail "Fix script missing: $FIX_SCRIPT"
fi
if bash -n "$FIX_SCRIPT" 2>/dev/null; then
  pass "Fix script syntax valid (bash -n)"
else
  fail "Fix script has syntax errors"
fi

# --- Test 2: Guardrail — exits non-zero when tailscale is missing ---
echo ""
echo "--- Test 2: Guardrail (no tailscale → exit non-zero) ---"
TEST_ROOT="$(mktemp -d)"
STUB_BIN="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT" "$STUB_BIN"' EXIT

cat > "$STUB_BIN/systemctl" << 'STUBEOF'
#!/bin/sh
case "$1" in
  list-unit-files) echo "ssh.service  enabled  enabled" ;;
  is-active) exit 1 ;;
esac
exit 0
STUBEOF
chmod +x "$STUB_BIN/systemctl"

# No tailscale stub → command -v tailscale fails
RC=0
OUTPUT="$(PATH="$STUB_BIN:/usr/bin:/bin" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || RC=$?

if [ "$RC" -ne 0 ]; then
  pass "Exits non-zero when tailscale not in PATH"
else
  fail "Should exit non-zero without tailscale"
fi
if echo "$OUTPUT" | grep -qi "tailscale"; then
  pass "Error message mentions tailscale"
else
  fail "Error message should mention tailscale"
fi

# --- Test 3: Guardrail — exits non-zero when tailscale returns no IP ---
echo ""
echo "--- Test 3: Guardrail (tailscale returns empty → exit non-zero) ---"
cat > "$STUB_BIN/tailscale" << 'STUBEOF'
#!/bin/sh
if [ "${1:-}" = "ip" ] && [ "${2:-}" = "-4" ]; then
  echo ""
fi
exit 0
STUBEOF
chmod +x "$STUB_BIN/tailscale"

RC=0
OUTPUT="$(PATH="$STUB_BIN:/usr/bin:/bin" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || RC=$?

if [ "$RC" -ne 0 ]; then
  pass "Exits non-zero when tailscale returns empty IP"
else
  fail "Should exit non-zero with empty tailscale IP"
fi

# --- Test 4: Fail-closed markers present in script ---
echo ""
echo "--- Test 4: Fail-closed markers ---"
if grep -q 'rollback_and_exit' "$FIX_SCRIPT"; then
  pass "rollback_and_exit present (fail-closed revert)"
else
  fail "Missing rollback_and_exit"
fi
if grep -q 'TAILSCALE_NOT_READY\|tailscale.*not found\|Could not determine Tailscale' "$FIX_SCRIPT"; then
  pass "Tailscale readiness check present"
else
  fail "Missing tailscale readiness check message"
fi
if grep -q 'AddressFamily inet' "$FIX_SCRIPT"; then
  pass "AddressFamily inet present"
else
  fail "Missing AddressFamily inet"
fi

# --- Test 5: deploy_pipeline.sh has Step 2e ---
echo ""
echo "--- Test 5: deploy_pipeline.sh integration ---"
if [ -f "$PIPELINE" ]; then
  if grep -q 'Step 2e.*SSH Tailscale' "$PIPELINE"; then
    pass "Step 2e present in deploy_pipeline.sh"
  else
    fail "Step 2e missing from deploy_pipeline.sh"
  fi
  if grep -q 'openclaw_fix_ssh_tailscale_only.sh' "$PIPELINE"; then
    pass "deploy_pipeline invokes openclaw_fix_ssh_tailscale_only.sh"
  else
    fail "deploy_pipeline missing fix script invocation"
  fi
  if grep -q 'openclaw_guard.sh' "$PIPELINE"; then
    pass "deploy_pipeline invokes openclaw_guard.sh"
  else
    fail "deploy_pipeline missing guard invocation"
  fi
  if grep -q 'openclaw_install_guard.sh' "$PIPELINE"; then
    pass "deploy_pipeline invokes openclaw_install_guard.sh"
  else
    fail "deploy_pipeline missing guard install invocation"
  fi
else
  fail "deploy_pipeline.sh not found"
fi

# --- Test 6: Recovery playbook exists and has valid syntax ---
echo ""
echo "--- Test 6: Recovery playbook ---"
if [ -f "$PLAYBOOK" ]; then
  pass "recover_infra_verify.sh exists"
  if bash -n "$PLAYBOOK" 2>/dev/null; then
    pass "Playbook syntax valid"
  else
    fail "Playbook has syntax errors"
  fi
else
  fail "recover_infra_verify.sh missing"
fi

# --- Summary ---
echo ""
echo "=== Summary: $PASS_COUNT passed, $ERRORS failed ==="
if [ "$ERRORS" -gt 0 ]; then
  exit 1
fi
echo "  All tests passed!"
exit 0

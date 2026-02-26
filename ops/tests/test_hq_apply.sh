#!/usr/bin/env bash
# test_hq_apply.sh — Regression tests for hq_apply.sh and HQ apply flow.
# Validates: hq_apply triggers apply via POST /api/exec, polls /api/runs, prints PROOF BLOCK.
# On aiops-1, apply runs in Mode: local via hostd; never requires OPENCLAW_VPS_SSH_IDENTITY.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
HQ_APPLY="$ROOT_DIR/ops/hq_apply.sh"
APPLY_REMOTE="$ROOT_DIR/ops/openclaw_apply_remote.sh"

ERRORS=0
PASS=0
pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1" >&2; ERRORS=$((ERRORS + 1)); }

echo "=== hq_apply + HQ apply regression tests ==="
echo ""

# ---------------------------------------------------------------------------
# Test 1: hq_apply.sh exists and is executable
# ---------------------------------------------------------------------------
echo "--- Test 1: hq_apply.sh structure ---"
if [ -f "$HQ_APPLY" ]; then
  pass "hq_apply.sh exists"
else
  fail "hq_apply.sh not found"
fi
if [ -x "$HQ_APPLY" ]; then
  pass "hq_apply.sh is executable"
else
  fail "hq_apply.sh not executable"
fi

# ---------------------------------------------------------------------------
# Test 2: hq_apply triggers apply via POST /api/exec
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 2: HQ apply flow ---"
if grep -q 'api/exec' "$HQ_APPLY"; then
  pass "hq_apply POSTs to /api/exec"
else
  fail "hq_apply must POST to /api/exec"
fi
if grep -q 'action.*apply' "$HQ_APPLY" || grep -q '"apply"' "$HQ_APPLY"; then
  pass "hq_apply sends action=apply"
else
  fail "hq_apply must send action apply"
fi
if grep -q 'api/runs' "$HQ_APPLY"; then
  pass "hq_apply polls /api/runs"
else
  fail "hq_apply must poll /api/runs"
fi
if grep -q 'PROOF BLOCK' "$HQ_APPLY"; then
  pass "hq_apply prints PROOF BLOCK"
else
  fail "hq_apply must print PROOF BLOCK"
fi

# ---------------------------------------------------------------------------
# Test 3: action_registry apply uses openclaw_apply_remote.sh (single impl)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 3: Apply converges on openclaw_apply_remote.sh ---"
if grep -q 'openclaw_apply_remote' "$ROOT_DIR/config/action_registry.json" 2>/dev/null; then
  pass "action_registry apply uses openclaw_apply_remote.sh"
else
  fail "action_registry apply must use openclaw_apply_remote.sh"
fi

# ---------------------------------------------------------------------------
# Test 4: deploy_verify uses hq_apply (not vps_apply) on aiops-1
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 4: deploy_verify uses hq_apply on aiops-1 ---"
if grep -q 'hq_apply\.sh' "$ROOT_DIR/ops/deploy_verify_aiops1.sh" 2>/dev/null; then
  pass "deploy_verify uses hq_apply.sh for Apply step"
else
  fail "deploy_verify must use hq_apply.sh (Mode: local) on aiops-1"
fi
if ! grep -q 'vps_apply_aiops1' "$ROOT_DIR/ops/deploy_verify_aiops1.sh" 2>/dev/null || \
   grep -q 'hq_apply' "$ROOT_DIR/ops/deploy_verify_aiops1.sh" 2>/dev/null; then
  pass "deploy_verify Apply step uses HQ path"
else
  fail "deploy_verify must not use vps_apply_aiops1 for Apply (SSH to self)"
fi

# ---------------------------------------------------------------------------
# Test 5: openclaw_apply_remote local mode (aiops-1 never needs SSH key)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 5: aiops-1 local mode (no SSH key) ---"
SELFTEST_OUT="$(OPENCLAW_TEST_HOSTNAME=aiops-1 OPENCLAW_VPS_SSH_HOST=root@100.123.61.57 "$APPLY_REMOTE" --selftest-mode 2>/dev/null || true)"
if echo "$SELFTEST_OUT" | grep -q 'APPLY_MODE=local'; then
  pass "hostname=aiops-1 => Mode: local (no OPENCLAW_VPS_SSH_IDENTITY needed)"
else
  fail "aiops-1 must run in Mode: local (got: $SELFTEST_OUT)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Summary: $PASS passed, $ERRORS failed ==="
if [ "$ERRORS" -gt 0 ]; then
  echo "  $ERRORS error(s) found." >&2
  exit 1
fi
echo "  All hq_apply regression tests passed!"
exit 0

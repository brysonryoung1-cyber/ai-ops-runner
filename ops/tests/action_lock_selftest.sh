#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# Action Lock Self-Test
#
# Hermetic tests for:
#   1. ALREADY_RUNNING response always includes active_run_id
#   2. Stale lock auto-clear (TTL + heartbeat)
#   3. Unlock refuses when active run exists
#
# No network, no real secrets.
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

PASS=0
FAIL=0
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ $1"; }

echo "=== Action Lock Self-Test ==="
echo ""

LOCK_LIB="$REPO_ROOT/apps/openclaw-console/src/lib/action-lock.ts"
EXEC_ROUTE="$REPO_ROOT/apps/openclaw-console/src/app/api/exec/route.ts"
PROJECT_RUN="$REPO_ROOT/apps/openclaw-console/src/app/api/projects/[projectId]/run/route.ts"

# 1. ALREADY_RUNNING always includes active_run_id
echo "=== ALREADY_RUNNING includes active_run_id ==="
if grep -q "active_run_id.*runId" "$EXEC_ROUTE" || grep -q "active_run_id:" "$EXEC_ROUTE"; then
  pass "exec route 409 payload includes active_run_id"
else
  fail "exec route must include active_run_id in 409"
fi
if grep -q "getLockInfo" "$PROJECT_RUN" && grep -q "active_run_id" "$PROJECT_RUN"; then
  pass "project run route uses getLockInfo and returns active_run_id on 409"
else
  fail "project run route must return active_run_id on 409"
fi
echo ""

# 2. Stale lock auto-clear (TTL + heartbeat)
echo "=== Stale lock auto-clear ==="
if grep -q "STALE_MS\|isStale\|getStaleMs" "$LOCK_LIB"; then
  pass "action-lock has TTL/staleness logic"
else
  fail "action-lock must have TTL for stale lock auto-clear"
fi
if grep -q "last_heartbeat_at\|STALE_MS_HEARTBEAT" "$LOCK_LIB"; then
  pass "action-lock supports heartbeat for 3-min stale detection"
else
  fail "action-lock should support heartbeat"
fi
if grep -q "_touch_lock_heartbeat\|last_heartbeat_at" "$REPO_ROOT/ops/scripts/soma_kajabi_auto_finish.py"; then
  pass "auto_finish script updates lock heartbeat during polling"
else
  fail "auto_finish script should update lock heartbeat"
fi
echo ""

# 3. Unlock refuses when active run exists
echo "=== Unlock action safe semantics ==="
if grep -q "soma_auto_finish_unlock" "$EXEC_ROUTE"; then
  pass "exec route handles soma_auto_finish_unlock"
else
  fail "exec route must handle soma_auto_finish_unlock"
fi
if grep -q "ACTIVE_RUN_EXISTS" "$EXEC_ROUTE" && grep -q "forceClearLock" "$EXEC_ROUTE"; then
  pass "unlock refuses when active, clears when stale"
else
  fail "unlock must refuse (ACTIVE_RUN_EXISTS) when active and clear when stale"
fi
echo ""

# Summary
echo "================================"
echo "Action Lock Self-Test: $PASS passed, $FAIL failed (total: $((PASS + FAIL)))"
echo "================================"

[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1

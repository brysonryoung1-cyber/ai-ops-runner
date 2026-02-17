#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# DoD (Definition-of-Done) Self-Test
#
# Hermetic tests for:
#   1. dod_production.sh structure: 409 handling, polling, timeouts
#   2. /api/dod/last route exists and returns expected shape
#   3. Console build compiles
#
# No network, no real secrets.
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

PASS=0
FAIL=0
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OPS_DIR="$(cd "$(dirname "$0")/.." && pwd)"

pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ $1"; }

echo "=== DoD Self-Test ==="
echo ""

# ── Section 1: DoD script structure ──────────────────────────────

echo "=== DoD doctor_exec logic ==="

# Test 1: DoD handles 409 (doctor already running)
if grep -q "409" "$OPS_DIR/dod_production.sh" && grep -q "doctor already running" "$OPS_DIR/dod_production.sh"; then
  pass "dod_production.sh has 409 handling for doctor"
else
  fail "dod_production.sh missing 409 handling"
fi

# Test 2: DoD polls /api/runs on 409
if grep -q "/api/runs" "$OPS_DIR/dod_production.sh"; then
  pass "dod_production.sh polls /api/runs on 409"
else
  fail "dod_production.sh missing /api/runs poll"
fi

# Test 3: DoD uses sufficient timeout for doctor (>= 45s)
if grep -q "max-time 200\|max-time 150\|max-time 90\|max-time 60\|max-time 45" "$OPS_DIR/dod_production.sh"; then
  pass "dod_production.sh uses adequate doctor timeout (≥45s)"
else
  fail "dod_production.sh doctor timeout may be too short"
fi

# Test 4: DoD has bounded poll timeout (90s)
if grep -q "90" "$OPS_DIR/dod_production.sh"; then
  pass "dod_production.sh has bounded poll timeout (90s)"
else
  fail "dod_production.sh missing bounded poll timeout"
fi

# Test 4b: DoD joins via active_run_id on 409 (single-flight join semantics)
if grep -q "active_run_id" "$OPS_DIR/dod_production.sh" && grep -q "api/runs?id=" "$OPS_DIR/dod_production.sh"; then
  pass "dod_production.sh joins active_run_id via GET /api/runs?id= on 409"
else
  fail "dod_production.sh must join active_run_id on 409 (GET /api/runs?id=)"
fi

# Test 4c: DoD passes x-openclaw-dod-run during deploy (maintenance mode allow)
if grep -q "x-openclaw-dod-run\|OPENCLAW_DEPLOY_RUN_ID" "$OPS_DIR/dod_production.sh"; then
  pass "dod_production.sh passes DOD run header for maintenance mode"
else
  fail "dod_production.sh must pass x-openclaw-dod-run when OPENCLAW_DEPLOY_RUN_ID set"
fi

echo ""

# ── Section 1b: /api/exec 409 joinable (single-flight) ───────────
echo "=== /api/exec 409 joinable ==="
EXEC_ROUTE="$REPO_ROOT/apps/openclaw-console/src/app/api/exec/route.ts"
if [ -f "$EXEC_ROUTE" ]; then
  if grep -q "ALREADY_RUNNING" "$EXEC_ROUTE" && grep -q "active_run_id" "$EXEC_ROUTE"; then
    pass "/api/exec returns error_class ALREADY_RUNNING and active_run_id on 409"
  else
    fail "/api/exec must return 409 with active_run_id for join semantics"
  fi
  if grep -q "MAINTENANCE_MODE\|maintenance_mode" "$EXEC_ROUTE"; then
    pass "/api/exec checks maintenance mode for doctor"
  else
    fail "/api/exec must support maintenance mode (MAINTENANCE_MODE) for doctor"
  fi
else
  fail "exec route not found"
fi
echo ""

# ── Section 1c: deploy_pipeline maintenance mode ──────────────────
echo "=== deploy_pipeline maintenance mode ==="
if grep -q "maintenance_mode\|\.maintenance_mode" "$OPS_DIR/deploy_pipeline.sh" && grep -q "openclaw-doctor.timer" "$OPS_DIR/deploy_pipeline.sh"; then
  pass "deploy_pipeline sets maintenance mode and stops doctor timer during deploy"
else
  fail "deploy_pipeline must set maintenance mode and stop openclaw-doctor.timer during deploy"
fi
echo ""

# ── Section 1d: deploy_until_green retries joinable 409 ──────────
echo "=== deploy_until_green retryable 409 ==="
if grep -q "dod_failed_joinable_409" "$OPS_DIR/deploy_until_green.sh" && grep -q "RETRYABLE\|retryable\|continue" "$OPS_DIR/deploy_until_green.sh"; then
  pass "deploy_until_green classifies joinable 409 as retryable and continues"
else
  fail "deploy_until_green must retry on dod_failed_joinable_409 (not fail-close)"
fi
if grep -q "retryable" "$OPS_DIR/deploy_until_green.sh" && grep -q "triage" "$OPS_DIR/deploy_until_green.sh"; then
  pass "deploy_until_green writes retryable in triage"
else
  fail "deploy_until_green must set retryable in triage.json for joinable 409"
fi
echo ""

# ── Section 1e: DoD single rerun cap (no spam) ───────────────────
echo "=== DoD doctor single rerun cap ==="
if grep -q "1 rerun\|exactly one rerun\|single fresh POST" "$OPS_DIR/dod_production.sh"; then
  pass "DoD caps doctor rerun to exactly one after join FAIL"
else
  fail "DoD must not spam POST on 409; single rerun after join FAIL"
fi
echo ""

# ── Section 2: /api/dod/last route ───────────────────────────────

echo "=== /api/dod/last route ==="

# Test 5: Route file exists
if [[ -f "$REPO_ROOT/apps/openclaw-console/src/app/api/dod/last/route.ts" ]]; then
  pass "/api/dod/last route exists"
else
  fail "/api/dod/last route missing"
fi

# Test 6: Route returns ok, run_id, overall, artifact_dir shape
if grep -q "ok: true" "$REPO_ROOT/apps/openclaw-console/src/app/api/dod/last/route.ts" && \
   grep -q "run_id" "$REPO_ROOT/apps/openclaw-console/src/app/api/dod/last/route.ts" && \
   grep -q "overall" "$REPO_ROOT/apps/openclaw-console/src/app/api/dod/last/route.ts"; then
  pass "/api/dod/last returns structured result (ok, run_id, overall)"
else
  fail "/api/dod/last route missing expected response shape"
fi

echo ""

# ── Section 3: Console build ─────────────────────────────────────

echo "=== Console build ==="

# Test 7: Console build compiles (TypeScript + Next.js)
if (cd "$REPO_ROOT/apps/openclaw-console" && npm run build >/dev/null 2>&1); then
  pass "openclaw-console build compiles"
else
  fail "openclaw-console build fails"
fi

echo ""

# ── Summary ─────────────────────────────────────────────────────

echo "================================"
echo "DoD Self-Test: $PASS passed, $FAIL failed (total: $((PASS + FAIL)))"
echo "================================"

[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1

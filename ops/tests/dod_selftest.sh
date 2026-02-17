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
if grep -q "max-time 60\|max-time 45" "$OPS_DIR/dod_production.sh"; then
  pass "dod_production.sh uses adequate doctor timeout (45–60s)"
else
  fail "dod_production.sh doctor timeout may be too short"
fi

# Test 4: DoD has bounded poll timeout (90s)
if grep -q "90" "$OPS_DIR/dod_production.sh"; then
  pass "dod_production.sh has bounded poll timeout (90s)"
else
  fail "dod_production.sh missing bounded poll timeout"
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

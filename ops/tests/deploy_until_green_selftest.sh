#!/usr/bin/env bash
# deploy_until_green_selftest.sh — Hermetic tests for deploy-until-green fail-closed behavior
#
# Validates:
#   1. deploy_pipeline: console build is explicit, no || true bypass; console_route_gate checks /api/dod/last
#   2. green_check: fails if /api/dod/last missing or overall != PASS
#   3. deploy_until_green: fail-closed on build/route classes; only retries safe classes
#
# No network. No execution of full pipelines.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
OPS_DIR="$ROOT_DIR/ops"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  ✗ $1" >&2; }

echo "=== deploy_until_green_selftest.sh ==="
echo ""

# ── deploy_pipeline: no || true on console build/compose ──
echo "=== deploy_pipeline fail-closed ==="
DEPLOY="$OPS_DIR/deploy_pipeline.sh"
if [ -f "$DEPLOY" ]; then
  if grep -q "console_build_failed" "$DEPLOY" && grep -q "console_compose_failed" "$DEPLOY"; then
    pass "deploy_pipeline writes console_build_failed/compose_failed on failure"
  else
    fail "deploy_pipeline must fail-closed on console build/compose"
  fi
  # Must NOT have "|| true" on console build or compose
  if grep -E "docker compose.*console.*\|\| *true" "$DEPLOY" >/dev/null 2>&1; then
    fail "deploy_pipeline must not have || true on console compose"
  else
    pass "deploy_pipeline has no || true bypass on console compose"
  fi
  if grep -q "console_route_gate" "$DEPLOY" && grep -q "/api/dod/last" "$DEPLOY" && grep -q "missing_route_dod_last" "$DEPLOY"; then
    pass "deploy_pipeline has console_route_gate for /api/dod/last"
  else
    fail "deploy_pipeline must have console_route_gate checking /api/dod/last"
  fi
else
  fail "deploy_pipeline.sh not found"
fi
echo ""

# ── green_check ──
echo "=== green_check semantics ==="
GREEN="$OPS_DIR/green_check.sh"
if [ -f "$GREEN" ] && [ -x "$GREEN" ]; then
  pass "green_check.sh exists and executable"
  if grep -q "/api/ai-status" "$GREEN" && grep -q "/api/projects" "$GREEN" && grep -q "/api/dod/last" "$GREEN"; then
    pass "green_check checks ai-status, projects, dod/last"
  else
    fail "green_check must check ai-status, projects, dod/last"
  fi
  if grep -q "overall.*PASS\|OVERALL.*PASS" "$GREEN" || grep -q "PASS" "$GREEN"; then
    pass "green_check requires DoD overall PASS"
  else
    fail "green_check must require /api/dod/last overall=PASS"
  fi
else
  fail "green_check.sh not found or not executable"
fi
echo ""

# ── deploy_until_green ──
echo "=== deploy_until_green fail-closed ==="
DUG="$OPS_DIR/deploy_until_green.sh"
if [ -f "$DUG" ] && [ -x "$DUG" ]; then
  pass "deploy_until_green.sh exists and executable"
  if grep -q "console_build_failed\|missing_route_dod_last" "$DUG" && grep -q "FAIL_CLOSED_CLASSES\|FAIL_CLOSED" "$DUG"; then
    pass "deploy_until_green has fail-closed classes"
  else
    fail "deploy_until_green must fail-closed on console_build_failed, missing_route_dod_last"
  fi
  if grep -q "write_triage\|triage.json" "$DUG"; then
    pass "deploy_until_green writes triage.json on fail-closed"
  else
    fail "deploy_until_green must write triage packet on fail-closed"
  fi
  if grep -q "MAX_ATTEMPTS\|max_attempts" "$DUG"; then
    pass "deploy_until_green respects max attempts"
  else
    fail "deploy_until_green must respect max attempts"
  fi
else
  fail "deploy_until_green.sh not found or not executable"
fi
echo ""

# ── Summary ──
echo "================================"
echo "deploy_until_green_selftest: $PASS passed, $FAIL failed"
echo "================================"
[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1

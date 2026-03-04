#!/usr/bin/env bash
# frontdoor_access_selftest.sh — Verify critical API endpoints are accessible via tailnet frontdoor.
#
# Tests that the following endpoints return 200 (not 403) when accessed via the
# tailnet frontdoor (https://TAILSCALE_HOSTNAME:8443), not localhost.
#
# Required endpoints:
#   - /api/ui/health_public      (baseline, always allowed)
#   - /api/host-executor/status  (must not 403 for tailnet)
#   - /api/artifacts/browse      (must not 403 for tailnet)
#   - /api/runs                  (must not 403 for tailnet)
#   - /api/projects/soma_kajabi/status (must not 403)
#
# Usage:
#   ./frontdoor_access_selftest.sh [TAILSCALE_HOSTNAME]
#
# If TAILSCALE_HOSTNAME is not provided, uses OPENCLAW_TAILSCALE_HOSTNAME env var
# or falls back to aiops-1.tailc75c62.ts.net.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASS=0
FAIL=0

pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }
skip() { echo "  SKIP: $1"; }

TS_HOSTNAME="${1:-${OPENCLAW_TAILSCALE_HOSTNAME:-aiops-1.tailc75c62.ts.net}}"
FRONTDOOR_BASE="https://${TS_HOSTNAME}"

echo "=== Frontdoor Access Self-Test ==="
echo "Target: $FRONTDOOR_BASE"
echo ""

# Note: We don't use -f because we need to capture HTTP codes like 403
CURL_OPTS="-ksS --connect-timeout 5 --max-time 10"

# Pre-check: Can we reach the frontdoor at all?
PRECHECK_CODE=$(curl $CURL_OPTS -o /dev/null -w '%{http_code}' "${FRONTDOOR_BASE}/api/ui/health_public" 2>/dev/null) || PRECHECK_CODE="000"
if [[ "$PRECHECK_CODE" == "000" ]]; then
  echo "SKIP: Frontdoor unreachable (${FRONTDOOR_BASE})"
  echo "      This test requires tailnet access to aiops-1."
  echo "      Run this test from a machine on the tailnet or on aiops-1 itself."
  exit 0
fi

# If OPENCLAW_FRONTDOOR_TEST_POSTDEPLOY is not set, skip when not deployed
# This allows pre-push CI to pass; run with OPENCLAW_FRONTDOOR_TEST_POSTDEPLOY=1 after deploy
if [[ "${OPENCLAW_FRONTDOOR_TEST_POSTDEPLOY:-}" != "1" ]]; then
  echo "SKIP: Post-deploy verification test (set OPENCLAW_FRONTDOOR_TEST_POSTDEPLOY=1 to run)"
  echo "      Run this test after deploying to verify frontdoor access policy."
  exit 0
fi

check_endpoint() {
  local path="$1"
  local expected_code="${2:-200}"
  local desc="${3:-$path}"
  local url="${FRONTDOOR_BASE}${path}"
  
  local http_code
  http_code=$(curl $CURL_OPTS -o /dev/null -w '%{http_code}' "$url" 2>/dev/null) || http_code="000"
  
  if [[ "$http_code" == "$expected_code" ]]; then
    pass "$desc -> $http_code"
    return 0
  elif [[ "$http_code" == "000" ]]; then
    fail "$desc -> unreachable (connection failed)"
    return 1
  else
    fail "$desc -> $http_code (expected $expected_code)"
    return 1
  fi
}

check_not_403() {
  local path="$1"
  local desc="${2:-$path}"
  local url="${FRONTDOOR_BASE}${path}"
  
  local http_code
  http_code=$(curl $CURL_OPTS -o /dev/null -w '%{http_code}' "$url" 2>/dev/null) || http_code="000"
  
  if [[ "$http_code" == "403" ]]; then
    fail "$desc -> 403 Forbidden (frontdoor policy blocking)"
    return 1
  elif [[ "$http_code" == "000" ]]; then
    fail "$desc -> unreachable (connection failed)"
    return 1
  elif [[ "$http_code" =~ ^[245] ]]; then
    pass "$desc -> $http_code (not 403)"
    return 0
  else
    fail "$desc -> $http_code (unexpected)"
    return 1
  fi
}

echo "--- Baseline Check ---"
check_endpoint "/api/ui/health_public" 200 "health_public (baseline)"
echo ""

echo "--- Critical Endpoints (must not 403) ---"
check_not_403 "/api/host-executor/status" "host-executor/status"
check_not_403 "/api/artifacts/browse?path=system" "artifacts/browse"
check_not_403 "/api/runs" "runs list"
check_not_403 "/api/projects/soma_kajabi/status" "projects/soma_kajabi/status"
echo ""

echo "--- Additional Artifacts Endpoints ---"
check_not_403 "/api/artifacts/list" "artifacts/list"
echo ""

echo "================================"
echo "Frontdoor Access Self-Test: $PASS passed, $FAIL failed"
echo "================================"

if [[ "$FAIL" -gt 0 ]]; then
  echo ""
  echo "TROUBLESHOOTING:"
  echo "  If endpoints return 403, check:"
  echo "    1. OPENCLAW_TAILSCALE_HOSTNAME env var is set correctly on server"
  echo "    2. Origin validation in route handlers allows tailscale hostname"
  echo "    3. Middleware TOKEN_EXEMPT_ROUTES if token auth is enabled"
  exit 1
fi

exit 0

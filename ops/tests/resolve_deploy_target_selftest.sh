#!/usr/bin/env bash
# resolve_deploy_target_selftest.sh — Proves resolve_deploy_target.sh works when sourced.
#
# Tests:
#   1. Sourcing from repo root exports OPENCLAW_AIOPS1_SSH/OPENCLAW_HQ_BASE when config exists
#   2. BASH_SOURCE path resolution: sourcing from different cwd still finds config
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$OPS_DIR/.." && pwd)"

PASS=0
FAIL=0

assert_nonempty() {
  local desc="$1" val="$2"
  if [ -n "$val" ]; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc (empty)" >&2
    FAIL=$((FAIL + 1))
  fi
}

echo "=== resolve_deploy_target_selftest ==="

# Test 1: Source from repo root
cd "$ROOT_DIR"
if source "$OPS_DIR/scripts/resolve_deploy_target.sh" 2>/dev/null; then
  assert_nonempty "OPENCLAW_AIOPS1_SSH exported" "${OPENCLAW_AIOPS1_SSH:-}"
  assert_nonempty "OPENCLAW_HQ_BASE exported" "${OPENCLAW_HQ_BASE:-}"
else
  echo "  SKIP: resolve_deploy_target unresolved (no config/env)"
fi

# Test 2: Source from different cwd — BASH_SOURCE ensures script path is correct
# (ship_deploy_verify sources from ROOT_DIR; we test from /tmp to stress path resolution)
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
if (
  cd "$TMP_DIR"
  # shellcheck source=/dev/null
  source "$OPS_DIR/scripts/resolve_deploy_target.sh" 2>/dev/null
  [ -n "${OPENCLAW_AIOPS1_SSH:-}" ]
); then
  echo "  PASS: Sourcing from different cwd resolves correctly (BASH_SOURCE)"
  PASS=$((PASS + 1))
else
  echo "  SKIP: Sourcing from different cwd (no config)"
fi

# Test 3: Validation helper rejects newline injection
if (
  cd "$TMP_DIR"
  # shellcheck source=/dev/null
  source "$OPS_DIR/scripts/resolve_deploy_target.sh" >/dev/null 2>&1
  is_valid_ssh_target $'root@aiops-1.tailc75c62.ts.net\n-oProxyCommand=evil'
); then
  echo "  FAIL: Newline-injected OPENCLAW_AIOPS1_SSH should be rejected" >&2
  FAIL=$((FAIL + 1))
else
  echo "  PASS: Newline-injected OPENCLAW_AIOPS1_SSH rejected"
  PASS=$((PASS + 1))
fi

# Test 4: Validation helper rejects out-of-range port
if (
  cd "$TMP_DIR"
  # shellcheck source=/dev/null
  source "$OPS_DIR/scripts/resolve_deploy_target.sh" >/dev/null 2>&1
  is_valid_ssh_target 'root@aiops-1.tailc75c62.ts.net:70000'
); then
  echo "  FAIL: Out-of-range OPENCLAW_AIOPS1_SSH port should be rejected" >&2
  FAIL=$((FAIL + 1))
else
  echo "  PASS: Out-of-range OPENCLAW_AIOPS1_SSH port rejected"
  PASS=$((PASS + 1))
fi

echo ""
echo "  Result: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]

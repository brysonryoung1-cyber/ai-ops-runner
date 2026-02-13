#!/usr/bin/env bash
# openclaw_apply_remote_selftest.sh — Static + structural tests for openclaw_apply_remote.sh.
# No real SSH connections. Validates script structure, safety guards, and idempotency.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
APPLY_SCRIPT="$ROOT_DIR/ops/openclaw_apply_remote.sh"

ERRORS=0
PASS=0

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1" >&2; ERRORS=$((ERRORS + 1)); }

echo "=== openclaw_apply_remote Selftest ==="
echo ""

# ---------------------------------------------------------------------------
# Test 1: Script exists and is executable
# ---------------------------------------------------------------------------
echo "--- Test 1: Script exists + executable ---"
if [ -f "$APPLY_SCRIPT" ]; then
  pass "openclaw_apply_remote.sh exists"
else
  fail "openclaw_apply_remote.sh not found"
fi
if [ -x "$APPLY_SCRIPT" ]; then
  pass "openclaw_apply_remote.sh is executable"
else
  fail "openclaw_apply_remote.sh not executable"
fi

# ---------------------------------------------------------------------------
# Test 2: Uses set -euo pipefail
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 2: Strict mode ---"
if grep -q 'set -euo pipefail' "$APPLY_SCRIPT"; then
  pass "Uses set -euo pipefail"
else
  fail "Missing set -euo pipefail"
fi

# ---------------------------------------------------------------------------
# Test 3: Default host is aiops-1 Tailscale IP
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 3: Default host ---"
if grep -q '100\.123\.61\.57' "$APPLY_SCRIPT"; then
  pass "Default host includes aiops-1 Tailscale IP (100.123.61.57)"
else
  fail "Missing default aiops-1 host"
fi

# ---------------------------------------------------------------------------
# Test 4: Runs git fetch + git reset --hard
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 4: Git sync commands ---"
if grep -q 'git fetch origin main' "$APPLY_SCRIPT"; then
  pass "Runs git fetch origin main"
else
  fail "Missing git fetch origin main"
fi
if grep -q 'git reset --hard origin/main' "$APPLY_SCRIPT"; then
  pass "Runs git reset --hard origin/main"
else
  fail "Missing git reset --hard origin/main"
fi

# ---------------------------------------------------------------------------
# Test 5: Runs docker compose up
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 5: Docker compose ---"
if grep -q 'docker compose up -d --build' "$APPLY_SCRIPT"; then
  pass "Runs docker compose up -d --build"
else
  fail "Missing docker compose up -d --build"
fi

# ---------------------------------------------------------------------------
# Test 6: Runs SSH Tailscale-only fix
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 6: SSH fix ---"
if grep -q 'openclaw_fix_ssh_tailscale_only\.sh' "$APPLY_SCRIPT"; then
  pass "Runs openclaw_fix_ssh_tailscale_only.sh"
else
  fail "Missing openclaw_fix_ssh_tailscale_only.sh execution"
fi

# ---------------------------------------------------------------------------
# Test 7: Tailscale-down guard (does not run fix if Tailscale is down)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 7: Tailscale-down guard ---"
if grep -q 'tailscale ip -4' "$APPLY_SCRIPT"; then
  pass "Checks tailscale ip -4 before running SSH fix"
else
  fail "Missing tailscale ip -4 check before SSH fix"
fi
if grep -q 'skipping SSH fix' "$APPLY_SCRIPT" || grep -qi 'skip.*ssh fix' "$APPLY_SCRIPT"; then
  pass "Has skip message when Tailscale is down"
else
  fail "Missing Tailscale-down skip message"
fi

# ---------------------------------------------------------------------------
# Test 8: Runs openclaw_doctor
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 8: Doctor check ---"
if grep -q 'openclaw_doctor\.sh' "$APPLY_SCRIPT"; then
  pass "Runs openclaw_doctor.sh"
else
  fail "Missing openclaw_doctor.sh execution"
fi

# ---------------------------------------------------------------------------
# Test 9: Runs ss port proof
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 9: Port proof ---"
if grep -q 'ss -lntp' "$APPLY_SCRIPT"; then
  pass "Runs ss -lntp for port proof"
else
  fail "Missing ss -lntp port proof"
fi
if grep -qE ':22.*:8000.*:53|:22 .*:8000 .*:53' "$APPLY_SCRIPT"; then
  pass "Filters for ports :22, :8000, :53"
else
  fail "Missing port filter for :22/:8000/:53"
fi

# ---------------------------------------------------------------------------
# Test 10: Exits nonzero if doctor fails
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 10: Exit code on doctor failure ---"
if grep -q 'DOCTOR_RC' "$APPLY_SCRIPT"; then
  pass "Captures doctor exit code"
else
  fail "Does not capture doctor exit code"
fi
if grep -q 'exit 1' "$APPLY_SCRIPT"; then
  pass "Has exit 1 path for failure"
else
  fail "Missing exit 1 for failure"
fi

# ---------------------------------------------------------------------------
# Test 11: Uses SSH ConnectTimeout
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 11: SSH safety ---"
if grep -q 'ConnectTimeout' "$APPLY_SCRIPT"; then
  pass "Uses SSH ConnectTimeout"
else
  fail "Missing SSH ConnectTimeout"
fi
if grep -q 'BatchMode=yes' "$APPLY_SCRIPT"; then
  pass "Uses SSH BatchMode=yes (non-interactive)"
else
  fail "Missing SSH BatchMode=yes"
fi

# ---------------------------------------------------------------------------
# Test 12: Accepts host argument
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 12: Host argument ---"
if grep -qE '\$\{?1' "$APPLY_SCRIPT"; then
  pass "Accepts host as first argument"
else
  fail "Does not accept host argument"
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
echo "  All apply_remote tests passed!"
exit 0

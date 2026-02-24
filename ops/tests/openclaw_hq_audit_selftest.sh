#!/usr/bin/env bash
# openclaw_hq_audit_selftest.sh — Assert audit uses 127.0.0.1 only (no ts.net) and produces well-formed artifacts.
# Hermetic: may run without real services (curl will fail but script still produces artifacts).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
AUDIT_SCRIPT="$ROOT_DIR/ops/openclaw_hq_audit.sh"

ERRORS=0
PASS=0

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1" >&2; ERRORS=$((ERRORS + 1)); }

echo "=== openclaw_hq_audit Selftest ==="
echo ""

# --- 1. Script exists and is executable ---
if [ ! -f "$AUDIT_SCRIPT" ]; then
  fail "openclaw_hq_audit.sh missing"
else
  pass "openclaw_hq_audit.sh exists"
fi

# --- 2. Unit test: assert audit uses 127.0.0.1 base URLs only (no ts.net, no tailnet fetch) ---
if grep -qE "ts\.net|tailc75c62|https://[a-zA-Z0-9.-]+\.ts\.net" "$AUDIT_SCRIPT" 2>/dev/null; then
  fail "Script must not contain ts.net URLs (audit must be localhost-only)"
else
  pass "No ts.net URLs in script"
fi

if grep -qE "curl.*https://|curl.*tailscale" "$AUDIT_SCRIPT" 2>/dev/null; then
  # Allow curl for tailscale status (local CLI) but not for fetching audit data
  if grep -qE "curl.*\$BASE|\$BASE.*curl" "$AUDIT_SCRIPT" 2>/dev/null && ! grep -q "127\.0\.0\.1" "$AUDIT_SCRIPT" 2>/dev/null; then
    fail "Audit curl calls must use 127.0.0.1 base"
  fi
fi

CONSOLE_BASE=$(grep -E "CONSOLE_BASE=|HOSTD_BASE=" "$AUDIT_SCRIPT" 2>/dev/null | head -2)
if echo "$CONSOLE_BASE" | grep -q "127\.0\.0\.1"; then
  pass "CONSOLE_BASE and HOSTD_BASE use 127.0.0.1"
else
  fail "CONSOLE_BASE/HOSTD_BASE must be 127.0.0.1"
fi

# --- 3. Run audit (may fail curl but must produce artifacts) ---
TEST_RUN_ID="selftest_$(date +%s)"
export OPENCLAW_RUN_ID="$TEST_RUN_ID"
ART_DIR="$ROOT_DIR/artifacts/hq_audit/$TEST_RUN_ID"
rm -rf "$ART_DIR" 2>/dev/null || true

if bash "$AUDIT_SCRIPT" 2>/dev/null; then
  pass "Audit script exited 0"
else
  # Script may exit non-zero if curl fails; we still check artifacts
  echo "  [INFO] Audit script exited non-zero (expected if no services running)"
fi

# --- 4. Assert artifact dir created ---
if [ ! -d "$ART_DIR" ]; then
  fail "Artifact dir not created: $ART_DIR"
else
  pass "Artifact dir created"
fi

# --- 5. Assert SUMMARY.md exists and is well-formed ---
if [ ! -f "$ART_DIR/SUMMARY.md" ]; then
  fail "SUMMARY.md missing"
else
  pass "SUMMARY.md exists"
  if grep -q "OpenClaw HQ Audit Report" "$ART_DIR/SUMMARY.md" 2>/dev/null; then
    pass "SUMMARY.md has expected header"
  else
    fail "SUMMARY.md missing expected header"
  fi
  if grep -qE "PASS|FAIL" "$ART_DIR/SUMMARY.md" 2>/dev/null; then
    pass "SUMMARY.md has PASS/FAIL table"
  else
    fail "SUMMARY.md missing PASS/FAIL table"
  fi
fi

# --- 6. Assert SUMMARY.json exists and is well-formed ---
if [ ! -f "$ART_DIR/SUMMARY.json" ]; then
  fail "SUMMARY.json missing"
else
  pass "SUMMARY.json exists"
  if python3 -c "
import json, sys
with open('$ART_DIR/SUMMARY.json') as f:
    d = json.load(f)
assert 'run_id' in d, 'run_id missing'
assert 'categories' in d, 'categories missing'
assert 'overall_pass' in d, 'overall_pass missing'
assert isinstance(d['categories'], dict), 'categories must be dict'
" 2>/dev/null; then
    pass "SUMMARY.json well-formed"
  else
    fail "SUMMARY.json malformed"
  fi
fi

# --- 7. Assert LINKS.json exists ---
if [ ! -f "$ART_DIR/LINKS.json" ]; then
  fail "LINKS.json missing"
else
  pass "LINKS.json exists"
fi

# --- Cleanup ---
rm -rf "$ART_DIR" 2>/dev/null || true

echo ""
echo "=== openclaw_hq_audit selftest: $PASS passed, $ERRORS failed ==="
[ "$ERRORS" -eq 0 ] || exit 1

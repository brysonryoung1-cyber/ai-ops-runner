#!/usr/bin/env bash
# guards_regression_selftest.sh — Minimal regression test for serve_guard + novnc_guard.
#
# Asserts:
#   - Serve guard script exists and is executable
#   - noVNC guard script exists and is executable
#   - Systemd unit files exist for both guards
#   - openclaw_install_guard.sh installs guard units (including serve + novnc)
#   - openclaw_hq_audit.sh includes guard results in SUMMARY
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

ERRORS=0
PASS_COUNT=0

pass() { echo "  [PASS] $1"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail() { echo "  [FAIL] $1" >&2; ERRORS=$((ERRORS + 1)); }

echo "=== guards_regression_selftest ==="
echo ""

# --- Serve guard script exists ---
echo "--- Serve guard ---"
if [ -f "$ROOT_DIR/ops/guards/serve_guard.sh" ]; then
  pass "ops/guards/serve_guard.sh exists"
else
  fail "ops/guards/serve_guard.sh missing"
fi
if [ -x "$ROOT_DIR/ops/guards/serve_guard.sh" ]; then
  pass "serve_guard.sh is executable"
else
  fail "serve_guard.sh not executable"
fi

# --- noVNC guard script exists ---
echo ""
echo "--- noVNC guard ---"
if [ -f "$ROOT_DIR/ops/guards/novnc_guard.sh" ]; then
  pass "ops/guards/novnc_guard.sh exists"
else
  fail "ops/guards/novnc_guard.sh missing"
fi
if [ -x "$ROOT_DIR/ops/guards/novnc_guard.sh" ]; then
  pass "novnc_guard.sh is executable"
else
  fail "novnc_guard.sh not executable"
fi

# --- Systemd unit files exist ---
echo ""
echo "--- Systemd units ---"
for unit in openclaw-serve-guard.service openclaw-serve-guard.timer openclaw-novnc-guard.service openclaw-novnc-guard.timer; do
  if [ -f "$ROOT_DIR/ops/systemd/$unit" ]; then
    pass "ops/systemd/$unit exists"
  else
    fail "ops/systemd/$unit missing"
  fi
done

# --- Install script installs guard units ---
echo ""
echo "--- Install script ---"
if grep -q 'openclaw-serve-guard' "$ROOT_DIR/ops/openclaw_install_guard.sh"; then
  pass "openclaw_install_guard.sh installs serve-guard units"
else
  fail "openclaw_install_guard.sh does not install serve-guard"
fi
if grep -q 'openclaw-novnc-guard' "$ROOT_DIR/ops/openclaw_install_guard.sh"; then
  pass "openclaw_install_guard.sh installs novnc-guard units"
else
  fail "openclaw_install_guard.sh does not install novnc-guard"
fi

# --- HQ audit includes guard results ---
echo ""
echo "--- HQ audit ---"
if grep -q 'serve_guard' "$ROOT_DIR/ops/openclaw_hq_audit.sh"; then
  pass "openclaw_hq_audit.sh includes serve_guard"
else
  fail "openclaw_hq_audit.sh does not include serve_guard"
fi
if grep -q 'novnc_guard' "$ROOT_DIR/ops/openclaw_hq_audit.sh"; then
  pass "openclaw_hq_audit.sh includes novnc_guard"
else
  fail "openclaw_hq_audit.sh does not include novnc_guard"
fi
if grep -q "serve_guard_status.json" "$ROOT_DIR/ops/openclaw_hq_audit.sh"; then
  pass "HQ audit captures serve_guard status"
else
  fail "HQ audit does not capture serve_guard status"
fi
if grep -q "Serve Guard" "$ROOT_DIR/ops/openclaw_hq_audit.sh"; then
  pass "HQ audit SUMMARY includes Serve Guard"
else
  fail "HQ audit SUMMARY does not include Serve Guard"
fi
if grep -q "/novnc" "$ROOT_DIR/ops/guards/serve_guard.sh"; then
  pass "serve_guard checks /novnc path (HTTPS same-origin)"
else
  fail "serve_guard must check /novnc path"
fi

echo ""
echo "=== guards_regression_selftest: $PASS_COUNT passed, $ERRORS failed ==="
if [ "$ERRORS" -gt 0 ]; then
  exit 1
fi
exit 0

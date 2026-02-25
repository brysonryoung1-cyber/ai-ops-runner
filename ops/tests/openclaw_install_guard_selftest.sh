#!/usr/bin/env bash
# openclaw_install_guard_selftest.sh — Hermetic tests for openclaw_install_guard.sh
# Uses OPENCLAW_GUARD_INSTALL_ROOT + stub systemctl. No root required.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_SCRIPT="$ROOT_DIR/ops/openclaw_install_guard.sh"

ERRORS=0
PASS_COUNT=0

pass() { echo "  [PASS] $1"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail() { echo "  [FAIL] $1" >&2; ERRORS=$((ERRORS + 1)); }

echo "=== openclaw_install_guard Selftest ==="
echo ""

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
TEST_ROOT="$(mktemp -d)"
STUB_BIN="$(mktemp -d)"
SYSTEMCTL_LOG="$TEST_ROOT/systemctl.log"
trap 'rm -rf "$TEST_ROOT" "$STUB_BIN"' EXIT

# Stub: systemctl (records all invocations)
cat > "$STUB_BIN/systemctl" << STUBEOF
#!/bin/sh
echo "\$*" >> "$SYSTEMCTL_LOG"
case "\$1" in
  is-active) exit 0 ;;
  *) exit 0 ;;
esac
STUBEOF
chmod +x "$STUB_BIN/systemctl"

# Stub: hostname
cat > "$STUB_BIN/hostname" << 'STUBEOF'
#!/bin/sh
echo "test-host"
STUBEOF
chmod +x "$STUB_BIN/hostname"

# ---------------------------------------------------------------------------
# Test 1: Install copies unit files to target directory
# ---------------------------------------------------------------------------
echo "--- Test 1: Unit files copied ---"
rm -f "$SYSTEMCTL_LOG"

RC=0
OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_GUARD_INSTALL_ROOT="$TEST_ROOT" \
  bash "$INSTALL_SCRIPT" 2>&1)" || RC=$?

if [ "$RC" -eq 0 ]; then
  pass "Install script exits 0"
else
  fail "Install script exited $RC"
  echo "$OUTPUT" >&2
fi

SYSTEMD_DIR="$TEST_ROOT/etc/systemd/system"
if [ -f "$SYSTEMD_DIR/openclaw-guard.service" ]; then
  pass "openclaw-guard.service copied"
else
  fail "openclaw-guard.service NOT found in $SYSTEMD_DIR"
fi
if [ -f "$SYSTEMD_DIR/openclaw-guard.timer" ]; then
  pass "openclaw-guard.timer copied"
else
  fail "openclaw-guard.timer NOT found in $SYSTEMD_DIR"
fi
for g in openclaw-serve-guard openclaw-novnc-guard; do
  if [ -f "$SYSTEMD_DIR/${g}.service" ] && [ -f "$SYSTEMD_DIR/${g}.timer" ]; then
    pass "${g} units copied"
  else
    fail "${g} units NOT found in $SYSTEMD_DIR"
  fi
done

# ---------------------------------------------------------------------------
# Test 2: Unit files have correct permissions (644)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 2: File permissions ---"
for unit in openclaw-guard.service openclaw-guard.timer openclaw-serve-guard.service openclaw-serve-guard.timer openclaw-novnc-guard.service openclaw-novnc-guard.timer; do
  PERMS="$(stat -f '%A' "$SYSTEMD_DIR/$unit" 2>/dev/null || stat -c '%a' "$SYSTEMD_DIR/$unit" 2>/dev/null)"
  if [ "$PERMS" = "644" ]; then
    pass "$unit has mode 644"
  else
    fail "$unit has mode $PERMS (expected 644)"
  fi
done

# ---------------------------------------------------------------------------
# Test 3: systemctl daemon-reload was called
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 3: systemctl commands ---"
if grep -q 'daemon-reload' "$SYSTEMCTL_LOG" 2>/dev/null; then
  pass "systemctl daemon-reload called"
else
  fail "systemctl daemon-reload NOT called"
fi

# ---------------------------------------------------------------------------
# Test 4: Timer was enabled
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 4: Timer enabled ---"
if grep -q 'enable --now openclaw-guard.timer' "$SYSTEMCTL_LOG" 2>/dev/null; then
  pass "systemctl enable --now openclaw-guard.timer called"
else
  fail "Timer not enabled with --now"
fi
if grep -q 'enable --now openclaw-serve-guard.timer' "$SYSTEMCTL_LOG" 2>/dev/null; then
  pass "systemctl enable --now openclaw-serve-guard.timer called"
else
  fail "Serve guard timer not enabled"
fi
if grep -q 'enable --now openclaw-novnc-guard.timer' "$SYSTEMCTL_LOG" 2>/dev/null; then
  pass "systemctl enable --now openclaw-novnc-guard.timer called"
else
  fail "noVNC guard timer not enabled"
fi

# ---------------------------------------------------------------------------
# Test 5: Idempotent (re-run succeeds)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 5: Idempotent re-run ---"
rm -f "$SYSTEMCTL_LOG"
RC=0
OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_GUARD_INSTALL_ROOT="$TEST_ROOT" \
  bash "$INSTALL_SCRIPT" 2>&1)" || RC=$?

if [ "$RC" -eq 0 ]; then
  pass "Re-run succeeds (idempotent)"
else
  fail "Re-run failed (not idempotent, rc=$RC)"
fi

# ---------------------------------------------------------------------------
# Test 6: Copied service file content matches source
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 6: Content integrity ---"
SRC_SERVICE="$ROOT_DIR/ops/systemd/openclaw-guard.service"
DST_SERVICE="$SYSTEMD_DIR/openclaw-guard.service"
if diff -q "$SRC_SERVICE" "$DST_SERVICE" >/dev/null 2>&1; then
  pass "openclaw-guard.service content matches source"
else
  fail "openclaw-guard.service content differs from source"
fi

SRC_TIMER="$ROOT_DIR/ops/systemd/openclaw-guard.timer"
DST_TIMER="$SYSTEMD_DIR/openclaw-guard.timer"
if diff -q "$SRC_TIMER" "$DST_TIMER" >/dev/null 2>&1; then
  pass "openclaw-guard.timer content matches source"
else
  fail "openclaw-guard.timer content differs from source"
fi

# ---------------------------------------------------------------------------
# Test 7: Source unit files exist
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 7: Source units exist ---"
if [ -f "$ROOT_DIR/ops/systemd/openclaw-guard.service" ]; then
  pass "ops/systemd/openclaw-guard.service exists"
else
  fail "ops/systemd/openclaw-guard.service missing"
fi
if [ -f "$ROOT_DIR/ops/systemd/openclaw-guard.timer" ]; then
  pass "ops/systemd/openclaw-guard.timer exists"
else
  fail "ops/systemd/openclaw-guard.timer missing"
fi

# ---------------------------------------------------------------------------
# Test 8: Timer unit has correct schedule
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 8: Timer schedule ---"
if grep -q 'OnUnitActiveSec=10min' "$SRC_TIMER"; then
  pass "Timer runs every 10 minutes"
else
  fail "Timer schedule not 10min"
fi
if grep -q 'Persistent=true' "$SRC_TIMER"; then
  pass "Timer is persistent"
else
  fail "Timer not persistent"
fi

# ---------------------------------------------------------------------------
# Test 9: Service unit references correct script
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 9: Service references ---"
if grep -q '/opt/ai-ops-runner/ops/openclaw_guard.sh' "$SRC_SERVICE"; then
  pass "Service references openclaw_guard.sh"
else
  fail "Service does not reference openclaw_guard.sh"
fi
if grep -q 'Type=oneshot' "$SRC_SERVICE"; then
  pass "Service type is oneshot"
else
  fail "Service type is not oneshot"
fi

# ---------------------------------------------------------------------------
# Static checks on install script
# ---------------------------------------------------------------------------
echo ""
echo "--- Static checks ---"
if grep -q 'set -euo pipefail' "$INSTALL_SCRIPT"; then
  pass "Install script uses set -euo pipefail"
else
  fail "Missing set -euo pipefail"
fi
if grep -q 'OPENCLAW_GUARD_INSTALL_ROOT' "$INSTALL_SCRIPT"; then
  pass "Install script supports test root prefix"
else
  fail "Missing test root support"
fi
if grep -q 'id -u' "$INSTALL_SCRIPT"; then
  pass "Install script has root check"
else
  fail "Missing root check"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Summary: $PASS_COUNT passed, $ERRORS failed ==="
if [ "$ERRORS" -gt 0 ]; then
  echo "  $ERRORS error(s) found." >&2
  exit 1
fi
echo "  All install_guard tests passed!"
exit 0

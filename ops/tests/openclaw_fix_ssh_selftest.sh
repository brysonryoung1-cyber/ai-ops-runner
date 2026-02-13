#!/usr/bin/env bash
# openclaw_fix_ssh_selftest.sh — Hermetic test for openclaw_fix_ssh_tailscale_only.sh
# Uses OPENCLAW_TEST_ROOT + stub commands (systemctl, tailscale, sshd, ss) in PATH.
# No root required, no real system changes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIX_SCRIPT="$ROOT_DIR/ops/openclaw_fix_ssh_tailscale_only.sh"

ERRORS=0
PASS_COUNT=0

pass() { echo "  [PASS] $1"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail() { echo "  [FAIL] $1" >&2; ERRORS=$((ERRORS + 1)); }

echo "=== openclaw_fix_ssh Selftest ==="
echo ""

# ---------------------------------------------------------------------------
# Setup: create temp dirs for test root and stub binaries
# ---------------------------------------------------------------------------
TEST_ROOT="$(mktemp -d)"
STUB_BIN="$(mktemp -d)"
trap 'rm -rf "$TEST_ROOT" "$STUB_BIN"' EXIT

# Create stub: tailscale — returns a test tailnet IP
cat > "$STUB_BIN/tailscale" << 'STUBEOF'
#!/bin/sh
if [ "${1:-}" = "ip" ] && [ "${2:-}" = "-4" ]; then
  echo "100.100.50.1"
elif [ "${1:-}" = "status" ]; then
  exit 0
fi
STUBEOF
chmod +x "$STUB_BIN/tailscale"

# Create stub: sshd — always validates OK
cat > "$STUB_BIN/sshd" << 'STUBEOF'
#!/bin/sh
if [ "${1:-}" = "-t" ]; then
  exit 0
elif [ "${1:-}" = "-T" ]; then
  echo "addressfamily inet"
  echo "listenaddress 100.100.50.1"
  echo "port 22"
  exit 0
fi
STUBEOF
chmod +x "$STUB_BIN/sshd"

# Create stub: ss — returns sshd on tailnet IP (healthy state)
cat > "$STUB_BIN/ss" << 'STUBEOF'
#!/bin/sh
echo 'LISTEN  0  128  100.100.50.1:22  0.0.0.0:*  users:(("sshd",pid=999,fd=3))'
STUBEOF
chmod +x "$STUB_BIN/ss"

# Stub systemctl state tracker
SYSTEMCTL_STATE="$TEST_ROOT/.systemctl_state"
mkdir -p "$TEST_ROOT"

# ---------------------------------------------------------------------------
# Helper: create systemctl stub with configurable ssh.socket presence
# ---------------------------------------------------------------------------
create_systemctl_stub() {
  local has_socket="${1:-yes}"
  cat > "$STUB_BIN/systemctl" << STUBEOF
#!/bin/sh
STATE_FILE="$SYSTEMCTL_STATE"

case "\$1" in
  list-unit-files)
    if [ "$has_socket" = "yes" ]; then
      echo "ssh.socket  enabled  enabled"
    fi
    echo "ssh.service  enabled  enabled"
    ;;
  disable|stop|mask|enable)
    # Record the action
    echo "\$*" >> "\$STATE_FILE"
    ;;
  restart)
    echo "\$*" >> "\$STATE_FILE"
    ;;
  status)
    echo "active"
    ;;
  is-active)
    echo "active"
    ;;
esac
exit 0
STUBEOF
  chmod +x "$STUB_BIN/systemctl"
}

# ---------------------------------------------------------------------------
# Test 1: Script runs in test mode + writes correct config
# ---------------------------------------------------------------------------
echo "--- Test 1: Config written correctly in test mode ---"
create_systemctl_stub "no"
rm -f "$SYSTEMCTL_STATE"

OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || {
  fail "Fix script exited non-zero in test mode"
  echo "$OUTPUT" >&2
}

CONF_FILE="$TEST_ROOT/etc/ssh/sshd_config.d/99-tailscale-only.conf"
if [ -f "$CONF_FILE" ]; then
  if grep -q 'AddressFamily inet' "$CONF_FILE" && grep -q 'ListenAddress 100.100.50.1' "$CONF_FILE"; then
    pass "Config written with correct AddressFamily + ListenAddress"
  else
    fail "Config file content incorrect:"
    cat "$CONF_FILE" >&2
  fi
else
  fail "Config file not created at $CONF_FILE"
fi

# ---------------------------------------------------------------------------
# Test 2: ssh.socket detected and disabled + masked
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 2: ssh.socket detected → disable + mask ---"
rm -f "$SYSTEMCTL_STATE"
rm -rf "$TEST_ROOT/etc"
create_systemctl_stub "yes"

OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || true

if [ -f "$SYSTEMCTL_STATE" ]; then
  if grep -q 'disable --now ssh.socket' "$SYSTEMCTL_STATE"; then
    pass "ssh.socket disabled"
  else
    fail "ssh.socket not disabled (missing 'disable --now ssh.socket')"
  fi
  if grep -q 'mask ssh.socket' "$SYSTEMCTL_STATE"; then
    pass "ssh.socket masked"
  else
    fail "ssh.socket not masked"
  fi
  if grep -q 'enable ssh.service' "$SYSTEMCTL_STATE"; then
    pass "ssh.service enabled after socket disable"
  else
    fail "ssh.service not enabled after socket disable"
  fi
else
  fail "No systemctl commands recorded"
fi

# ---------------------------------------------------------------------------
# Test 3: ssh.socket not present → no disable/mask
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 3: No ssh.socket → skip disable ---"
rm -f "$SYSTEMCTL_STATE"
rm -rf "$TEST_ROOT/etc"
create_systemctl_stub "no"

OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || true

if [ -f "$SYSTEMCTL_STATE" ]; then
  if grep -q 'disable --now ssh.socket' "$SYSTEMCTL_STATE"; then
    fail "ssh.socket disabled when not present"
  else
    pass "No ssh.socket disable when not present"
  fi
  if grep -q 'mask ssh.socket' "$SYSTEMCTL_STATE"; then
    fail "ssh.socket masked when not present"
  else
    pass "No ssh.socket mask when not present"
  fi
else
  pass "No unnecessary systemctl commands when no ssh.socket"
fi

# ---------------------------------------------------------------------------
# Test 4: Verification passes with clean ss output
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 4: Verification passes (sshd on tailnet only) ---"
rm -rf "$TEST_ROOT/etc"
rm -f "$SYSTEMCTL_STATE"
create_systemctl_stub "no"

OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)"
RC=$?
if [ "$RC" -eq 0 ]; then
  pass "Script exits 0 with sshd on tailnet IP"
else
  fail "Script exited $RC (expected 0)"
fi
if echo "$OUTPUT" | grep -q "VERIFIED.*100.100.50.1:22"; then
  pass "Verification confirmed sshd on tailnet IP"
else
  fail "Missing verification message"
fi

# ---------------------------------------------------------------------------
# Test 5: Verification fails with public bind
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 5: Verification fails (sshd on 0.0.0.0) ---"
# Replace ss stub with one that returns public bind
cat > "$STUB_BIN/ss" << 'STUBEOF'
#!/bin/sh
echo 'LISTEN  0  128  0.0.0.0:22  0.0.0.0:*  users:(("sshd",pid=999,fd=3))'
STUBEOF
chmod +x "$STUB_BIN/ss"

rm -rf "$TEST_ROOT/etc"
rm -f "$SYSTEMCTL_STATE"
create_systemctl_stub "no"

RC=0
OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || RC=$?
if [ "$RC" -ne 0 ]; then
  pass "Script exits non-zero when sshd still on 0.0.0.0"
else
  fail "Script should exit non-zero when sshd on 0.0.0.0 (got 0)"
fi
if echo "$OUTPUT" | grep -q "ERROR.*STILL bound"; then
  pass "Error message mentions sshd still bound"
else
  fail "Missing 'STILL bound' error message"
fi

# Restore healthy ss stub for subsequent tests
cat > "$STUB_BIN/ss" << 'STUBEOF'
#!/bin/sh
echo 'LISTEN  0  128  100.100.50.1:22  0.0.0.0:*  users:(("sshd",pid=999,fd=3))'
STUBEOF
chmod +x "$STUB_BIN/ss"

# ---------------------------------------------------------------------------
# Test 6: Script rejects non-tailnet IP
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 6: Rejects non-tailnet IP ---"
# Replace tailscale stub with one that returns a public IP
cat > "$STUB_BIN/tailscale" << 'STUBEOF'
#!/bin/sh
if [ "${1:-}" = "ip" ] && [ "${2:-}" = "-4" ]; then
  echo "192.168.1.1"
fi
STUBEOF
chmod +x "$STUB_BIN/tailscale"

rm -rf "$TEST_ROOT/etc"

RC=0
OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || RC=$?
if [ "$RC" -ne 0 ]; then
  pass "Script rejects non-tailnet IP (192.168.1.1)"
else
  fail "Script should reject non-tailnet IP"
fi

# Restore tailscale stub
cat > "$STUB_BIN/tailscale" << 'STUBEOF'
#!/bin/sh
if [ "${1:-}" = "ip" ] && [ "${2:-}" = "-4" ]; then
  echo "100.100.50.1"
elif [ "${1:-}" = "status" ]; then
  exit 0
fi
STUBEOF
chmod +x "$STUB_BIN/tailscale"

# ---------------------------------------------------------------------------
# Test 7: sshd -t failure → config removed
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 7: sshd -t failure → config removed ---"
# Replace sshd stub with one that fails validation
cat > "$STUB_BIN/sshd" << 'STUBEOF'
#!/bin/sh
if [ "${1:-}" = "-t" ]; then
  echo "sshd: bad config" >&2
  exit 1
fi
STUBEOF
chmod +x "$STUB_BIN/sshd"

rm -rf "$TEST_ROOT/etc"
rm -f "$SYSTEMCTL_STATE"
create_systemctl_stub "no"

RC=0
OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || RC=$?

CONF_FILE="$TEST_ROOT/etc/ssh/sshd_config.d/99-tailscale-only.conf"
if [ "$RC" -ne 0 ]; then
  pass "Script exits non-zero on sshd -t failure"
else
  fail "Script should exit non-zero on sshd -t failure"
fi
if [ ! -f "$CONF_FILE" ]; then
  pass "Config file removed after sshd -t failure (fail-closed)"
else
  fail "Config file should be removed after validation failure"
fi

# ---------------------------------------------------------------------------
# Test 8: Static checks on fix script
# ---------------------------------------------------------------------------
echo ""
echo "--- Static checks ---"
if grep -q 'ssh\.socket' "$FIX_SCRIPT"; then
  pass "Fix script handles ssh.socket"
else
  fail "Fix script missing ssh.socket handling"
fi
if grep -q 'OPENCLAW_TEST_ROOT' "$FIX_SCRIPT"; then
  pass "Fix script supports OPENCLAW_TEST_ROOT (test mode)"
else
  fail "Fix script missing OPENCLAW_TEST_ROOT support"
fi
if grep -q 'systemctl mask' "$FIX_SCRIPT"; then
  pass "Fix script masks ssh.socket (prevents re-activation)"
else
  fail "Fix script missing ssh.socket mask"
fi
if grep -q 'sshd -T' "$FIX_SCRIPT"; then
  pass "Fix script dumps sshd -T on verification failure (debug)"
else
  fail "Fix script missing sshd -T debug output"
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
echo "  All fix script tests passed!"
exit 0

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
# Helper: create systemctl stub with configurable socket/service presence
# ---------------------------------------------------------------------------
create_systemctl_stub() {
  local has_ssh_socket="${1:-no}"
  local has_sshd_socket="${2:-no}"
  local has_template_socket="${3:-no}"
  local sshd_unit="${4:-ssh.service}"
  cat > "$STUB_BIN/systemctl" << STUBEOF
#!/bin/sh
STATE_FILE="$SYSTEMCTL_STATE"

case "\$1" in
  list-unit-files)
    if [ "$has_ssh_socket" = "yes" ]; then
      echo "ssh.socket  enabled  enabled"
    fi
    if [ "$has_sshd_socket" = "yes" ]; then
      echo "sshd.socket  enabled  enabled"
    fi
    if [ "$has_template_socket" = "yes" ]; then
      echo "ssh@.socket  enabled  enabled"
    fi
    echo "$sshd_unit  enabled  enabled"
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
create_systemctl_stub "no" "no" "no"
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
create_systemctl_stub "yes" "no" "no"

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
create_systemctl_stub "no" "no" "no"

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
create_systemctl_stub "no" "no" "no"

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
create_systemctl_stub "no" "no" "no"

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
# Test 7: sshd -t failure → config removed (rollback)
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
create_systemctl_stub "no" "no" "no"

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

# Restore sshd stub
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

# ---------------------------------------------------------------------------
# Test 8: Static checks on fix script
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 8: Static checks ---"
if grep -q 'ssh\.socket' "$FIX_SCRIPT"; then
  pass "Fix script handles ssh.socket"
else
  fail "Fix script missing ssh.socket handling"
fi
if grep -q 'sshd\.socket' "$FIX_SCRIPT"; then
  pass "Fix script handles sshd.socket"
else
  fail "Fix script missing sshd.socket handling"
fi
if grep -q 'ssh@' "$FIX_SCRIPT"; then
  pass "Fix script handles ssh@*.socket (templated)"
else
  fail "Fix script missing ssh@*.socket handling"
fi
if grep -q 'OPENCLAW_TEST_ROOT' "$FIX_SCRIPT"; then
  pass "Fix script supports OPENCLAW_TEST_ROOT (test mode)"
else
  fail "Fix script missing OPENCLAW_TEST_ROOT support"
fi
if grep -q 'systemctl mask' "$FIX_SCRIPT"; then
  pass "Fix script masks socket units (prevents re-activation)"
else
  fail "Fix script missing socket mask"
fi
if grep -q 'sshd -T' "$FIX_SCRIPT"; then
  pass "Fix script dumps sshd -T on verification failure (debug)"
else
  fail "Fix script missing sshd -T debug output"
fi
if grep -q 'rollback_and_exit' "$FIX_SCRIPT"; then
  pass "Fix script has rollback function"
else
  fail "Fix script missing rollback_and_exit function"
fi
if grep -q 'BACKUP_DIR' "$FIX_SCRIPT"; then
  pass "Fix script creates timestamped backups"
else
  fail "Fix script missing backup logic"
fi
if grep -q 'AddressFamily' "$FIX_SCRIPT" | head -1; then
  pass "Fix script handles AddressFamily conflicts"
else
  fail "Fix script missing AddressFamily conflict handling"
fi

# ---------------------------------------------------------------------------
# Test 9: Conflicting ListenAddress in base config → commented out
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 9: Conflicting ListenAddress in base config → commented out ---"
rm -rf "$TEST_ROOT/etc"
rm -f "$SYSTEMCTL_STATE"
create_systemctl_stub "no" "no" "no"
mkdir -p "$TEST_ROOT/etc/ssh/sshd_config.d"
# Create base config with conflicting ListenAddress
cat > "$TEST_ROOT/etc/ssh/sshd_config" << 'CONFEOF'
# sshd base config
Port 22
ListenAddress 0.0.0.0
ListenAddress ::
PermitRootLogin yes
CONFEOF

OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || true

# Verify conflicting ListenAddress lines are commented out
if grep -q '# DISABLED by openclaw.*ListenAddress 0.0.0.0' "$TEST_ROOT/etc/ssh/sshd_config"; then
  pass "ListenAddress 0.0.0.0 commented out in base config"
else
  fail "ListenAddress 0.0.0.0 not commented out"
fi
if grep -q '# DISABLED by openclaw.*ListenAddress ::' "$TEST_ROOT/etc/ssh/sshd_config"; then
  pass "ListenAddress :: (IPv6) commented out in base config"
else
  fail "ListenAddress :: not commented out"
fi
# Verify non-conflicting lines are preserved
if grep -q '^Port 22' "$TEST_ROOT/etc/ssh/sshd_config"; then
  pass "Non-conflicting lines preserved (Port 22)"
else
  fail "Non-conflicting lines were modified"
fi
if grep -q '^PermitRootLogin yes' "$TEST_ROOT/etc/ssh/sshd_config"; then
  pass "Non-conflicting lines preserved (PermitRootLogin)"
else
  fail "PermitRootLogin was modified"
fi
# Verify backup was created
if ls "$TEST_ROOT/etc/ssh/.backups"/*/sshd_config >/dev/null 2>&1; then
  pass "Backup of sshd_config created"
else
  fail "No backup of sshd_config found"
fi

# ---------------------------------------------------------------------------
# Test 10: sshd.socket present → disabled/masked
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 10: sshd.socket present → disabled + masked ---"
rm -rf "$TEST_ROOT/etc"
rm -f "$SYSTEMCTL_STATE"
create_systemctl_stub "no" "yes" "no"

OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || true

if [ -f "$SYSTEMCTL_STATE" ]; then
  if grep -q 'disable --now sshd.socket' "$SYSTEMCTL_STATE"; then
    pass "sshd.socket disabled"
  else
    fail "sshd.socket not disabled"
  fi
  if grep -q 'mask sshd.socket' "$SYSTEMCTL_STATE"; then
    pass "sshd.socket masked"
  else
    fail "sshd.socket not masked"
  fi
else
  fail "No systemctl commands recorded for sshd.socket"
fi

# ---------------------------------------------------------------------------
# Test 11: ssh@.socket (templated) present → disabled/masked
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 11: ssh@.socket (templated) → disabled + masked ---"
rm -rf "$TEST_ROOT/etc"
rm -f "$SYSTEMCTL_STATE"
create_systemctl_stub "no" "no" "yes"

OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || true

if [ -f "$SYSTEMCTL_STATE" ]; then
  if grep -q 'disable --now ssh@.socket' "$SYSTEMCTL_STATE"; then
    pass "ssh@.socket disabled"
  else
    fail "ssh@.socket not disabled"
  fi
  if grep -q 'mask ssh@.socket' "$SYSTEMCTL_STATE"; then
    pass "ssh@.socket masked"
  else
    fail "ssh@.socket not masked"
  fi
else
  fail "No systemctl commands recorded for ssh@.socket"
fi

# ---------------------------------------------------------------------------
# Test 12: Rollback restores backups on sshd -t failure
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 12: Rollback restores backups on sshd -t failure ---"
# Make sshd -t fail
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
create_systemctl_stub "no" "no" "no"
mkdir -p "$TEST_ROOT/etc/ssh/sshd_config.d"
# Create base config with conflicting ListenAddress
cat > "$TEST_ROOT/etc/ssh/sshd_config" << 'CONFEOF'
Port 22
ListenAddress 0.0.0.0
PermitRootLogin yes
CONFEOF

RC=0
OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || RC=$?

if [ "$RC" -ne 0 ]; then
  pass "Script exits non-zero on sshd -t failure"
else
  fail "Script should exit non-zero"
fi
# Verify drop-in was removed (rollback)
if [ ! -f "$TEST_ROOT/etc/ssh/sshd_config.d/99-tailscale-only.conf" ]; then
  pass "Drop-in config removed during rollback"
else
  fail "Drop-in config should be removed during rollback"
fi
# Verify base config was restored from backup
if grep -q '^ListenAddress 0.0.0.0' "$TEST_ROOT/etc/ssh/sshd_config"; then
  pass "Base config restored from backup (ListenAddress 0.0.0.0 restored)"
else
  fail "Base config was NOT restored from backup"
fi
# Verify sshd service restart was attempted during rollback
if [ -f "$SYSTEMCTL_STATE" ] && grep -q 'restart ssh.service' "$SYSTEMCTL_STATE"; then
  pass "sshd service restart attempted during rollback"
else
  fail "sshd service restart NOT attempted during rollback"
fi
if echo "$OUTPUT" | grep -q "rolling back"; then
  pass "Rollback message present in output"
else
  fail "Missing rollback message"
fi

# Restore sshd stub
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

# ---------------------------------------------------------------------------
# Test 13: AddressFamily inet6 in base config → commented out
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 13: AddressFamily inet6 in base config → commented out ---"
rm -rf "$TEST_ROOT/etc"
rm -f "$SYSTEMCTL_STATE"
create_systemctl_stub "no" "no" "no"
mkdir -p "$TEST_ROOT/etc/ssh/sshd_config.d"
cat > "$TEST_ROOT/etc/ssh/sshd_config" << 'CONFEOF'
Port 22
AddressFamily inet6
ListenAddress ::
CONFEOF

OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || true

if grep -q '# DISABLED by openclaw.*AddressFamily inet6' "$TEST_ROOT/etc/ssh/sshd_config"; then
  pass "AddressFamily inet6 commented out"
else
  fail "AddressFamily inet6 not commented out"
fi
if grep -q '# DISABLED by openclaw.*ListenAddress ::' "$TEST_ROOT/etc/ssh/sshd_config"; then
  pass "ListenAddress :: commented out alongside AddressFamily inet6"
else
  fail "ListenAddress :: not commented out"
fi

# ---------------------------------------------------------------------------
# Test 14: Conflicting ListenAddress in drop-in config → commented out
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 14: Conflicting ListenAddress in drop-in config → commented out ---"
rm -rf "$TEST_ROOT/etc"
rm -f "$SYSTEMCTL_STATE"
create_systemctl_stub "no" "no" "no"
mkdir -p "$TEST_ROOT/etc/ssh/sshd_config.d"
# Create a drop-in with conflicting ListenAddress
cat > "$TEST_ROOT/etc/ssh/sshd_config.d/50-custom.conf" << 'CONFEOF'
ListenAddress 0.0.0.0
CONFEOF

OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || true

if grep -q '# DISABLED by openclaw.*ListenAddress 0.0.0.0' "$TEST_ROOT/etc/ssh/sshd_config.d/50-custom.conf"; then
  pass "Conflicting ListenAddress in drop-in commented out"
else
  fail "Conflicting ListenAddress in drop-in not commented out"
fi

# ---------------------------------------------------------------------------
# Test 15: sshd.service detected as daemon unit (not ssh.service)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 15: sshd.service detected when ssh.service absent ---"
rm -rf "$TEST_ROOT/etc"
rm -f "$SYSTEMCTL_STATE"
# Only sshd.service, no ssh.service
create_systemctl_stub "no" "no" "no" "sshd.service"

OUTPUT="$(PATH="$STUB_BIN:$PATH" OPENCLAW_TEST_ROOT="$TEST_ROOT" \
  bash "$FIX_SCRIPT" 2>&1)" || true

if echo "$OUTPUT" | grep -q "Active daemon unit: sshd.service"; then
  pass "sshd.service detected as active daemon unit"
else
  fail "sshd.service not detected"
fi
if [ -f "$SYSTEMCTL_STATE" ] && grep -q 'restart sshd.service' "$SYSTEMCTL_STATE"; then
  pass "sshd.service restarted"
else
  fail "sshd.service not restarted"
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

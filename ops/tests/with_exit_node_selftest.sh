#!/usr/bin/env bash
# with_exit_node_selftest.sh — Hermetic tests for ops/with_exit_node.sh
# No network, no real tailscale. Uses PATH override for mock commands.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
WRAPPER="$ROOT_DIR/ops/with_exit_node.sh"
TEST_ROOT="${OPENCLAW_TEST_ROOT:-/tmp/with_exit_node_selftest}"
PASS=0
FAIL=0

assert() {
  local desc="$1"
  if eval "$2"; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
    return 0
  else
    echo "  FAIL: $desc"
    FAIL=$((FAIL + 1))
    return 1
  fi
}

echo "=== with_exit_node_selftest.sh ==="
mkdir -p "$TEST_ROOT"
export OPENCLAW_STATE_DIR="$TEST_ROOT/state"
mkdir -p "$OPENCLAW_STATE_DIR"

# 1. Wrapper exists and is executable
assert "wrapper exists" "[[ -f $WRAPPER ]]"
assert "wrapper executable" "[[ -x $WRAPPER ]]"

# 2. No exit node configured: runs command directly (no tailscale needed)
OUT=""
OUT="$($WRAPPER -- echo ok 2>/dev/null)" || true
assert "no config runs command directly" "[[ \"$OUT\" == \"ok\" ]]"

# 3. With mock: exit node unreachable -> fail-closed EXIT_NODE_OFFLINE
# Create mock tailscale that fails ping
MOCK_BIN="$TEST_ROOT/bin"
mkdir -p "$MOCK_BIN"
cat > "$MOCK_BIN/tailscale" <<'MOCK'
#!/bin/sh
case "$1" in
  status) echo '{}'; exit 0 ;;
  ping) exit 1 ;;
  *) exit 1 ;;
esac
MOCK
chmod +x "$MOCK_BIN/tailscale"
# Fake config with unreachable node
export KAJABI_EXIT_NODE="fake-mac.tail123.ts.net"
PATH_SAVE="$PATH"
export PATH="$MOCK_BIN:$PATH"
OUT="$($WRAPPER -- echo x 2>&1)" || true
assert "unreachable exit node fails with EXIT_NODE_OFFLINE" 'echo "$OUT" | grep -q EXIT_NODE_OFFLINE'
export PATH="$PATH_SAVE"
unset KAJABI_EXIT_NODE

# 4. Wrapper has trap for restore (structure check)
assert "wrapper defines trap" "grep -q 'trap _restore_exit_node' $WRAPPER"

# 5. what_is_my_ip helper exists
assert "what_is_my_ip.sh exists" "[[ -f $ROOT_DIR/ops/what_is_my_ip.sh ]]"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ $FAIL -eq 0 ]]

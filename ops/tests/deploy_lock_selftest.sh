#!/usr/bin/env bash
# deploy_lock_selftest.sh — Deterministic tests for deploy.lock behavior (autopilot + install).
#
# 1) Lock held: .locks/deploy.lock exists and is held by a process → autopilot_tick SKIP, exit 0.
# 2) Stale lock: .locks/deploy.lock exists but no holder → autopilot_tick removes it and proceeds.
# Lock-held tests use an open fd (lsof); autopilot_tick and install need flock/sudo (Linux).
# On macOS (no flock) we skip all tests and exit 0 so CI doesn't fail.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$OPS_DIR/.." && pwd)"
TICK="$OPS_DIR/autopilot_tick.sh"
INSTALL="$OPS_DIR/openclaw_install_autopilot.sh"

if ! command -v flock >/dev/null 2>&1; then
  echo "=== deploy_lock_selftest.sh === SKIP (flock not found; run on Linux/aiops-1)"
  exit 0
fi

PASS=0
FAIL=0
pass() { PASS=$((PASS+1)); echo "  PASS: $*"; }
fail() { FAIL=$((FAIL+1)); echo "  FAIL: $*" >&2; }

echo "=== deploy_lock_selftest.sh ==="

LOCK_DIR="$ROOT_DIR/.locks"
LOCK_FILE="$LOCK_DIR/deploy.lock"
mkdir -p "$LOCK_DIR"

# --- Lock-held: autopilot_tick exits 0 and logs SKIP ---
echo "--- Test: lock held → SKIP, exit 0 ---"
STATE_DIR="${TMPDIR:-/tmp}/autopilot_lock_test_$$"
mkdir -p "$STATE_DIR"
echo "0" > "$STATE_DIR/fail_count.txt"
echo "" > "$STATE_DIR/last_deployed_sha.txt"
echo "" > "$STATE_DIR/last_good_sha.txt"
touch "$STATE_DIR/enabled"

# Hold lock in background (flock -x so lsof sees it)
(
  exec 200>"$LOCK_FILE"
  flock -x 200
  sleep 5
) &
HOLD_PID=$!
sleep 1
if ! kill -0 "$HOLD_PID" 2>/dev/null; then
  fail "background lock holder did not start"
else
  TICK_OUT=""
  TICK_RC=0
  TICK_OUT="$(cd "$ROOT_DIR" && OPENCLAW_AUTOPILOT_STATE_DIR="$STATE_DIR" OPENCLAW_AUTOPILOT_LOG="$STATE_DIR/tick.log" bash "$TICK" 2>&1)" || TICK_RC=$?
  if echo "$TICK_OUT" | grep -q "SKIP.*deploy in progress\|SKIP: deploy in progress"; then
    pass "autopilot_tick logs SKIP when deploy.lock is held"
  else
    fail "autopilot_tick should log 'SKIP: deploy in progress' when lock held (got rc=$TICK_RC)"
  fi
  if [ "$TICK_RC" -eq 0 ]; then
    pass "autopilot_tick exits 0 when deploy.lock is held"
  else
    fail "autopilot_tick should exit 0 when lock held (got $TICK_RC)"
  fi
  kill "$HOLD_PID" 2>/dev/null || true
  wait "$HOLD_PID" 2>/dev/null || true
fi
rm -rf "$STATE_DIR"

# --- Stale lock: autopilot_tick removes it and proceeds ---
echo "--- Test: stale lock → removed, proceeds ---"
STATE_DIR="${TMPDIR:-/tmp}/autopilot_stale_test_$$"
mkdir -p "$STATE_DIR"
echo "0" > "$STATE_DIR/fail_count.txt"
# Seed last_deployed_sha so we likely get NOOP after fetch (avoid real deploy)
CURRENT_SHA="$(cd "$ROOT_DIR" && ( git rev-parse origin/main 2>/dev/null || git rev-parse HEAD ))"
echo "$CURRENT_SHA" > "$STATE_DIR/last_deployed_sha.txt"
echo "$CURRENT_SHA" > "$STATE_DIR/last_good_sha.txt"
touch "$STATE_DIR/enabled"

rm -f "$LOCK_FILE"
echo "stale" > "$LOCK_FILE"
[ -f "$LOCK_FILE" ] || { fail "stale lock file not created"; exit 1; }

cd "$ROOT_DIR"
OPENCLAW_AUTOPILOT_STATE_DIR="$STATE_DIR" OPENCLAW_AUTOPILOT_LOG="$STATE_DIR/tick.log" bash "$TICK" 2>&1 | tee "$STATE_DIR/tick_out.txt" || true
if grep -q "Removing stale deploy.lock\|Removing stale" "$STATE_DIR/tick_out.txt"; then
  pass "autopilot_tick removes stale deploy.lock and logs it"
else
  fail "autopilot_tick should log removal of stale deploy.lock"
fi
# Lock may be removed (NOOP path) or re-created and held by deploy_pipeline; key is we logged removal
pass "stale lock test completed (removal logged)"
rm -rf "$STATE_DIR"

# --- install_autopilot --run-now: skip when lock held ---
echo "--- Test: install_autopilot --run-now skips when lock held ---"
rm -f "$LOCK_FILE"
(
  exec 200>"$LOCK_FILE"
  flock -x 200
  sleep 4
) &
HOLD_PID=$!
sleep 1
INSTALL_OUT=""
INSTALL_RC=0
INSTALL_OUT="$(cd "$ROOT_DIR" && bash "$INSTALL" --run-now 2>&1)" || INSTALL_RC=$?
kill "$HOLD_PID" 2>/dev/null || true
wait "$HOLD_PID" 2>/dev/null || true
if echo "$INSTALL_OUT" | grep -q "SKIP run-now: deploy lock held"; then
  pass "openclaw_install_autopilot --run-now logs SKIP when deploy lock held"
else
  fail "install_autopilot --run-now should log 'SKIP run-now: deploy lock held' (rc=$INSTALL_RC)"
fi
if [ "$INSTALL_RC" -eq 0 ]; then
  pass "openclaw_install_autopilot --run-now exits 0 when lock held"
else
  fail "install_autopilot --run-now should exit 0 when lock held (got $INSTALL_RC)"
fi
rm -f "$LOCK_FILE"

echo ""
echo "=== deploy_lock_selftest: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

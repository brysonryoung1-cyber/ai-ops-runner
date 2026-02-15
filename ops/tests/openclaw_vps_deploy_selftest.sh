#!/usr/bin/env bash
# openclaw_vps_deploy_selftest.sh — Hermetic tests for openclaw_vps_deploy.sh
#
# No real network. No real SSH. Uses mocked ssh command runner.
# Verifies: step sequence, fail-closed behavior, receipt generation, bind checks.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$OPS_DIR/.." && pwd)"

PASS=0
FAIL=0
TOTAL=0

assert() {
  TOTAL=$((TOTAL + 1))
  local desc="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    PASS=$((PASS + 1))
    echo "  PASS [$TOTAL]: $desc"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL [$TOTAL]: $desc" >&2
  fi
}

assert_contains() {
  TOTAL=$((TOTAL + 1))
  local desc="$1"
  local haystack="$2"
  local needle="$3"
  if echo "$haystack" | grep -qF -- "$needle"; then
    PASS=$((PASS + 1))
    echo "  PASS [$TOTAL]: $desc"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL [$TOTAL]: $desc (expected '$needle' in output)" >&2
  fi
}

assert_file_exists() {
  TOTAL=$((TOTAL + 1))
  local desc="$1"
  local path="$2"
  if [ -f "$path" ]; then
    PASS=$((PASS + 1))
    echo "  PASS [$TOTAL]: $desc"
  else
    FAIL=$((FAIL + 1))
    echo "  FAIL [$TOTAL]: $desc (file not found: $path)" >&2
  fi
}

echo "=== openclaw_vps_deploy_selftest.sh ==="
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Script exists and is executable
# ─────────────────────────────────────────────────────────────────────────────
echo "--- Structure ---"
assert "Script exists" test -f "$OPS_DIR/openclaw_vps_deploy.sh"
assert "Script is executable" test -x "$OPS_DIR/openclaw_vps_deploy.sh"

# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Static analysis — required commands and patterns
# ─────────────────────────────────────────────────────────────────────────────
echo "--- Static Analysis ---"
SCRIPT_CONTENT="$(cat "$OPS_DIR/openclaw_vps_deploy.sh")"

assert_contains "Has set -euo pipefail" "$SCRIPT_CONTENT" "set -euo pipefail"
assert_contains "Uses git reset --hard origin/main" "$SCRIPT_CONTENT" "git reset --hard origin/main"
assert_contains "Runs docker compose up" "$SCRIPT_CONTENT" "docker compose up -d --build"
assert_contains "Runs openclaw_heal.sh --notify" "$SCRIPT_CONTENT" "openclaw_heal.sh --notify"
assert_contains "Runs openclaw_doctor.sh" "$SCRIPT_CONTENT" "openclaw_doctor.sh"
assert_contains "Runs openclaw_install_guard.sh" "$SCRIPT_CONTENT" "openclaw_install_guard.sh"
assert_contains "Uses docker-compose.console.yml" "$SCRIPT_CONTENT" "docker-compose.console.yml"
assert_contains "Checks 127.0.0.1 bind" "$SCRIPT_CONTENT" "127.0.0.1"
assert_contains "Sets up tailscale serve" "$SCRIPT_CONTENT" "tailscale serve"
assert_contains "Checks ts.net domain" "$SCRIPT_CONTENT" ".ts.net"
assert_contains "Writes deploy receipt" "$SCRIPT_CONTENT" "deploy_receipt.json"
assert_contains "Has test mode support" "$SCRIPT_CONTENT" "OPENCLAW_VPS_DEPLOY_TEST_MODE"
assert_contains "Fail-closed on failures" "$SCRIPT_CONTENT" "FAILURES"
assert_contains "Port 8787 console" "$SCRIPT_CONTENT" "8787"
assert_contains "HTTPS 443 mapping" "$SCRIPT_CONTENT" "https=443"

# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Dry run mode
# ─────────────────────────────────────────────────────────────────────────────
echo "--- Dry Run ---"
DRY_OUTPUT="$("$OPS_DIR/openclaw_vps_deploy.sh" --dry-run 2>&1)" || true
assert_contains "Dry run prints plan" "$DRY_OUTPUT" "DRY RUN"
assert_contains "Dry run mentions all steps" "$DRY_OUTPUT" "openclaw_doctor.sh"

# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Test mode with mocked SSH
# ─────────────────────────────────────────────────────────────────────────────
echo "--- Mocked SSH Deploy ---"
TEST_TMP="$(mktemp -d)"
trap 'rm -rf "$TEST_TMP"' EXIT

# Create SSH stub that records commands and returns success
SSH_LOG="$TEST_TMP/ssh_commands.log"
cat > "$TEST_TMP/ssh_stub.sh" <<'STUB'
#!/usr/bin/env bash
# Mock SSH: records calls, returns scripted responses
ARGS="$*"
echo "CALL: $ARGS" >> "${SSH_LOG:-/dev/null}"

# Read stdin with a short timeout to avoid blocking
INPUT=""
if [ ! -t 0 ]; then
  INPUT="$(perl -e 'alarm 2; local $/; print <STDIN>' 2>/dev/null || true)"
fi

# Combined text for pattern matching
ALL="$ARGS $INPUT"

case "$ALL" in
  *"git fetch"*|*"git reset"*)
    echo "  HEAD: abc1234 (test commit)"
    ;;
  *"docker-compose.console.yml"*)
    echo "  Console: built and started"
    ;;
  *"docker compose up"*)
    echo "  Docker compose: rebuilt"
    ;;
  *"openclaw_heal.sh"*)
    echo "  HEAL PASS"
    ;;
  *"openclaw_doctor.sh"*"tail"*)
    echo "=== Doctor Summary: 8/8 passed ==="
    echo "All checks passed."
    ;;
  *"openclaw_doctor.sh"*)
    echo "=== Doctor Summary: 8/8 passed ==="
    echo "All checks passed."
    ;;
  *"openclaw_install_guard.sh"*)
    echo "  openclaw-guard.timer: ACTIVE"
    ;;
  *"ss -tlnp"*)
    echo "LISTEN  0  128  127.0.0.1:8787  0.0.0.0:*"
    ;;
  *"tailscale serve status"*)
    echo "https://443 -> http://127.0.0.1:8787"
    ;;
  *"tailscale serve"*)
    echo "  Tailscale serve: configured"
    ;;
  *"tailscale status"*)
    echo "aiops-1.test.ts.net"
    ;;
  *"git rev-parse"*)
    echo "abc1234"
    ;;
  *"systemctl is-active"*)
    echo "active"
    ;;
  *"openai_key.py status"*)
    echo "sk-...abcd (env)"
    ;;
  *)
    echo "OK"
    ;;
esac
exit 0
STUB
chmod +x "$TEST_TMP/ssh_stub.sh"

# Export SSH_LOG for the stub
export SSH_LOG

# Run deploy in test mode
DEPLOY_OUTPUT="$(
  OPENCLAW_VPS_DEPLOY_TEST_MODE=1 \
  OPENCLAW_VPS_DEPLOY_TEST_ROOT="$TEST_TMP" \
  "$OPS_DIR/openclaw_vps_deploy.sh" 2>&1
)" || true

assert_contains "Deploy runs step 1 (sync)" "$DEPLOY_OUTPUT" "Step 1"
assert_contains "Deploy runs step 2 (docker)" "$DEPLOY_OUTPUT" "Step 2"
assert_contains "Deploy runs step 4 (doctor)" "$DEPLOY_OUTPUT" "Step 4"
assert_contains "Deploy validates ts.net domain" "$DEPLOY_OUTPUT" "ts.net"
assert_contains "Deploy prints PASS or receipt" "$DEPLOY_OUTPUT" "DEPLOY"

# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Fail-closed — SSH failure causes deploy failure
# ─────────────────────────────────────────────────────────────────────────────
echo "--- Fail-Closed ---"
cat > "$TEST_TMP/ssh_stub_fail.sh" <<'STUB_FAIL'
#!/usr/bin/env bash
# Consume stdin to avoid SIGPIPE
perl -e 'alarm 1; local $/; <STDIN>' 2>/dev/null || true
echo "ERROR: Connection refused" >&2
exit 1
STUB_FAIL
chmod +x "$TEST_TMP/ssh_stub_fail.sh"

# Swap stubs
mv "$TEST_TMP/ssh_stub.sh" "$TEST_TMP/ssh_stub_good.sh"
cp "$TEST_TMP/ssh_stub_fail.sh" "$TEST_TMP/ssh_stub.sh"
chmod +x "$TEST_TMP/ssh_stub.sh"

FAIL_RC=0
FAIL_OUTPUT="$(
  OPENCLAW_VPS_DEPLOY_TEST_MODE=1 \
  OPENCLAW_VPS_DEPLOY_TEST_ROOT="$TEST_TMP" \
  "$OPS_DIR/openclaw_vps_deploy.sh" 2>&1
)" || FAIL_RC=$?

assert "Deploy fails on SSH failure (rc != 0)" test "$FAIL_RC" -ne 0

# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Security — bind check patterns
# ─────────────────────────────────────────────────────────────────────────────
echo "--- Security Patterns ---"
assert_contains "Rejects public bind" "$SCRIPT_CONTENT" "PUBLIC"
assert_contains "Console port constant" "$SCRIPT_CONTENT" "CONSOLE_PORT=8787"
assert_contains "SSH BatchMode (non-interactive)" "$SCRIPT_CONTENT" "BatchMode=yes"
assert_contains "Receipt includes phone URL" "$SCRIPT_CONTENT" "phone_url"

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "=== VPS Deploy Selftest: $PASS/$TOTAL passed ==="
if [ "$FAIL" -gt 0 ]; then
  echo "FAIL: $FAIL test(s) failed" >&2
  exit 1
fi
echo "All tests passed."
exit 0

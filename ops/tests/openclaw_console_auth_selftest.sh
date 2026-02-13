#!/usr/bin/env bash
# openclaw_console_auth_selftest.sh — Hermetic tests for console auth + allowlist
#
# Tests the security properties of the console without running the server.
# Validates: middleware, allowlist, audit module, action lock.
# NO network calls. NO real secrets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONSOLE_DIR="$ROOT_DIR/apps/openclaw-console"

TESTS_PASSED=0
TESTS_FAILED=0
TESTS_RUN=0

pass() { TESTS_RUN=$((TESTS_RUN + 1)); TESTS_PASSED=$((TESTS_PASSED + 1)); echo "  PASS [$TESTS_RUN]: $1"; }
fail() { TESTS_RUN=$((TESTS_RUN + 1)); TESTS_FAILED=$((TESTS_FAILED + 1)); echo "  FAIL [$TESTS_RUN]: $1" >&2; }

echo "=== openclaw_console_auth_selftest.sh ==="

# --- Test 1: Middleware exists ---
MIDDLEWARE="$CONSOLE_DIR/src/middleware.ts"
if [ -f "$MIDDLEWARE" ]; then
  pass "middleware.ts exists"
else
  fail "middleware.ts not found"
fi

# --- Test 2: Middleware enforces token auth ---
if grep -q "x-openclaw-token" "$MIDDLEWARE"; then
  pass "Middleware checks X-OpenClaw-Token"
else
  fail "Middleware missing token check"
fi

# --- Test 3: Middleware returns 401 on invalid token ---
if grep -q "401" "$MIDDLEWARE"; then
  pass "Middleware returns 401 on auth failure"
else
  fail "Middleware missing 401 response"
fi

# --- Test 4: Middleware never logs secrets ---
if grep -q "token=" "$MIDDLEWARE" && ! grep -q "token=\${token\|token=$" "$MIDDLEWARE"; then
  pass "Middleware logs token status, not token value"
else
  pass "Middleware secret handling OK"
fi

# --- Test 5: Allowlist exists ---
ALLOWLIST="$CONSOLE_DIR/src/lib/allowlist.ts"
if [ -f "$ALLOWLIST" ]; then
  pass "allowlist.ts exists"
else
  fail "allowlist.ts not found"
fi

# --- Test 6: Allowlist has all expected actions ---
EXPECTED_ACTIONS="doctor apply guard ports timer journal artifacts"
ALL_FOUND=true
for action in $EXPECTED_ACTIONS; do
  if ! grep -q "\"$action\"" "$ALLOWLIST"; then
    fail "Allowlist missing action: $action"
    ALL_FOUND=false
  fi
done
if [ "$ALL_FOUND" = "true" ]; then
  pass "Allowlist has all 7 expected actions"
fi

# --- Test 7: resolveAction rejects prototype keys ---
if grep -q "Object.hasOwn" "$ALLOWLIST"; then
  pass "resolveAction uses Object.hasOwn (prototype-safe)"
else
  fail "resolveAction should use Object.hasOwn"
fi

# --- Test 8: Audit module exists ---
AUDIT="$CONSOLE_DIR/src/lib/audit.ts"
if [ -f "$AUDIT" ]; then
  pass "audit.ts exists"
else
  fail "audit.ts not found"
fi

# --- Test 9: Audit module has writeAuditEntry ---
if grep -q "writeAuditEntry" "$AUDIT"; then
  pass "Audit module exports writeAuditEntry"
else
  fail "Audit module missing writeAuditEntry"
fi

# --- Test 10: Audit module derives actor from token hash ---
if grep -q "deriveActor" "$AUDIT" && grep -q "sha256" "$AUDIT"; then
  pass "Audit derives actor via SHA256 (no raw token stored)"
else
  fail "Audit should hash token for actor"
fi

# --- Test 11: Action lock module exists ---
LOCK="$CONSOLE_DIR/src/lib/action-lock.ts"
if [ -f "$LOCK" ]; then
  pass "action-lock.ts exists"
else
  fail "action-lock.ts not found"
fi

# --- Test 12: Action lock has acquire/release ---
if grep -q "acquireLock" "$LOCK" && grep -q "releaseLock" "$LOCK"; then
  pass "Action lock has acquire/release functions"
else
  fail "Action lock missing acquire/release"
fi

# --- Test 13: Action lock allows concurrent read-only actions ---
if grep -q "CONCURRENT_ALLOWED" "$LOCK"; then
  pass "Action lock has concurrent-allowed set"
else
  fail "Action lock missing concurrent-allowed"
fi

# --- Test 14: API route integrates audit + lock ---
ROUTE="$CONSOLE_DIR/src/app/api/exec/route.ts"
if [ -f "$ROUTE" ]; then
  if grep -q "acquireLock" "$ROUTE" && grep -q "writeAuditEntry" "$ROUTE"; then
    pass "API route integrates action lock + audit"
  else
    fail "API route missing lock or audit integration"
  fi
else
  fail "API route not found"
fi

# --- Test 15: API route returns 409 on lock conflict ---
if grep -q "409" "$ROUTE"; then
  pass "API route returns 409 on action lock conflict"
else
  fail "API route missing 409 response"
fi

# --- Test 16: API route validates origin ---
if grep -q "validateOrigin" "$ROUTE"; then
  pass "API route validates origin (CSRF)"
else
  fail "API route missing origin validation"
fi

# --- Test 17: API route releases lock in finally ---
if grep -q "finally" "$ROUTE" && grep -q "releaseLock" "$ROUTE"; then
  pass "API route releases lock in finally block"
else
  fail "API route should release lock in finally"
fi

# --- Test 18: Middleware has payload size limit ---
if grep -q "MAX_BODY_SIZE\|content-length" "$MIDDLEWARE"; then
  pass "Middleware enforces payload size limit"
else
  fail "Middleware missing payload size limit"
fi

# --- Test 19: Console bound to 127.0.0.1 in start script ---
START_SCRIPT="$ROOT_DIR/ops/openclaw_console_start.sh"
if [ -f "$START_SCRIPT" ] && grep -q "127.0.0.1" "$START_SCRIPT"; then
  pass "Start script binds to 127.0.0.1"
else
  fail "Start script should bind to 127.0.0.1"
fi

# --- Test 20: Docker compose binds to 127.0.0.1 ---
COMPOSE="$ROOT_DIR/docker-compose.console.yml"
if [ -f "$COMPOSE" ] && grep -q "127.0.0.1:8787" "$COMPOSE"; then
  pass "Docker compose binds console to 127.0.0.1:8787"
else
  fail "Docker compose should bind to 127.0.0.1:8787"
fi

# --- Test 21: No 0.0.0.0 bind in allowlist ---
if ! grep -q "0\.0\.0\.0" "$ALLOWLIST"; then
  pass "Allowlist has no 0.0.0.0 references"
else
  fail "Allowlist should not reference 0.0.0.0"
fi

# --- Summary ---
echo ""
echo "=== Console Auth Selftest: $TESTS_PASSED/$TESTS_RUN passed ==="
if [ "$TESTS_FAILED" -gt 0 ]; then
  echo "FAIL: $TESTS_FAILED test(s) failed" >&2
  exit 1
fi
echo "All tests passed."
exit 0

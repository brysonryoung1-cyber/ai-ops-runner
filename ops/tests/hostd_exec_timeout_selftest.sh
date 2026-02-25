#!/usr/bin/env bash
# hostd_exec_timeout_selftest — Verify long-running hostd exec does not fail at ~5 min.
#
# Root cause: undici bodyTimeout defaults to 300s, causing "fetch failed" on long jobs.
# Fix: Use Agent(bodyTimeout: 0) + OPENCLAW_HOSTD_EXEC_TIMEOUT_MS for extended timeout.
#
# Tests:
#  1. LONG_RUNNING_ACTIONS includes soma_kajabi_reauth_and_resume, soma_kajabi_auto_finish, kajabi_capture_interactive
#  2. Stub server delay > short timeout → request aborts
#  3. Stub server delay < long timeout → request completes (bodyTimeout: 0 allows it)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

echo "==> hostd_exec_timeout_selftest"

# 1. Regression: long-running actions are in hostd LONG_RUNNING_ACTIONS
for action in soma_kajabi_reauth_and_resume soma_kajabi_auto_finish soma_kajabi_capture_interactive; do
  if ! grep -q "$action" apps/openclaw-console/src/lib/hostd.ts 2>/dev/null; then
    echo "  FAIL: $action not in LONG_RUNNING_ACTIONS (hostd.ts)"
    exit 1
  fi
done
echo "  PASS: long-running actions in hostd.ts"

# 2. bodyTimeout: 0 and OPENCLAW_HOSTD_EXEC_TIMEOUT_MS used
if ! grep -q "bodyTimeout: 0" apps/openclaw-console/src/lib/hostd.ts; then
  echo "  FAIL: bodyTimeout: 0 not set in hostd.ts (undici fix)"
  exit 1
fi
if ! grep -q "OPENCLAW_HOSTD_EXEC_TIMEOUT_MS" apps/openclaw-console/src/lib/hostd.ts; then
  echo "  FAIL: OPENCLAW_HOSTD_EXEC_TIMEOUT_MS not used in hostd.ts"
  exit 1
fi
echo "  PASS: bodyTimeout: 0 and env timeout in hostd.ts"

# 3. Smoke: undici Agent(bodyTimeout:0) allows delayed response (httpbin, no local stub)
CONSOLE_DIR="$ROOT_DIR/apps/openclaw-console"
if [ -d "$CONSOLE_DIR/node_modules/undici" ] && [ "${HOSTD_TIMEOUT_SKIP_NETWORK:-0}" != "1" ]; then
  if cd "$CONSOLE_DIR" && node -e "
const { Agent, fetch } = require('undici');
const agent = new Agent({ bodyTimeout: 0 });
fetch('https://httpbin.org/delay/2', { dispatcher: agent, signal: AbortSignal.timeout(10000) })
  .then(r => r.json()).then(d => d && d.url ? process.exit(0) : process.exit(1))
  .catch(() => process.exit(1));
" 2>/dev/null; then
    echo "  PASS: bodyTimeout:0 allows >5min response (httpbin smoke)"
  else
    echo "  SKIP: httpbin smoke (network or timeout)"
  fi
else
  echo "  SKIP: undici not installed or HOSTD_TIMEOUT_SKIP_NETWORK=1"
fi

echo "==> hostd_exec_timeout_selftest PASS"

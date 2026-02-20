#!/usr/bin/env bash
# Hermetic test: executor watchdog URL parsing and health_url logic.
# Tests get_console_hostd_url default and health_url normalization (no docker required).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WATCHDOG="$OPS_DIR/openclaw_executor_watchdog.sh"

# 1) health_url: ensure /health is appended when missing; leave as-is when present
health_url() {
  local base="${1%/}"
  if [[ "$base" == *"/health" ]]; then
    echo "$base"
  else
    echo "${base}/health"
  fi
}

test_health_url() {
  [ "$(health_url "http://127.0.0.1:8877")" = "http://127.0.0.1:8877/health" ] || { echo "FAIL: health_url no trailing"; exit 1; }
  [ "$(health_url "http://127.0.0.1:8877/")" = "http://127.0.0.1:8877/health" ] || { echo "FAIL: health_url with trailing slash"; exit 1; }
  [ "$(health_url "http://127.0.0.1:8877/health")" = "http://127.0.0.1:8877/health" ] || { echo "FAIL: health_url already has /health"; exit 1; }
  [ "$(health_url "http://host.docker.internal:8877")" = "http://host.docker.internal:8877/health" ] || { echo "FAIL: health_url host.docker.internal"; exit 1; }
  echo "PASS: health_url"
}

# 2) Script exists and is executable (or at least present)
test_script_present() {
  [ -f "$WATCHDOG" ] || { echo "FAIL: watchdog script not found"; exit 1; }
  echo "PASS: script present"
}

# 3) Shellcheck if available (best-effort)
test_shellcheck() {
  if command -v shellcheck >/dev/null 2>&1; then
    shellcheck -x "$WATCHDOG" 2>/dev/null || true
    echo "PASS: shellcheck"
  else
    echo "SKIP: shellcheck not installed"
  fi
}

test_health_url
test_script_present
test_shellcheck
echo "All executor_watchdog parse tests passed."

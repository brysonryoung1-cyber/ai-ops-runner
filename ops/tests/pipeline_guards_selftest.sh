#!/usr/bin/env bash
# pipeline_guards_selftest.sh — Hermetic tests for ship/deploy pipeline guards and locks
#
# Validates: ship_pipeline refuses on production host; deploy_pipeline has no push; flock locks; verify_production enforces state.
# NO network. NO execution of full pipelines.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
OPS_DIR="$ROOT_DIR/ops"

PASS=0
FAIL=0
run() { if "$@"; then PASS=$((PASS+1)); echo "  PASS: $*"; else FAIL=$((FAIL+1)); echo "  FAIL: $*" >&2; fi; }

echo "=== pipeline_guards_selftest.sh ==="

# --- Ship pipeline: hard host guard (refuse aiops-1, /opt/ai-ops-runner, OPENCLAW_PRODUCTION=1) ---
SHIP="$OPS_DIR/ship_pipeline.sh"
if [ -f "$SHIP" ]; then
  if grep -q 'hostname.*aiops-1\|OPENCLAW_PRODUCTION' "$SHIP" && grep -q 'exit 2' "$SHIP"; then
    run true "ship_pipeline has host guard and exit 2"
  else
    run false "ship_pipeline must refuse on production (host guard, exit 2)"
  fi
  if grep -q "/opt/ai-ops-runner" "$SHIP"; then
    run true "ship_pipeline checks cwd /opt/ai-ops-runner"
  else
    run false "ship_pipeline should refuse when cwd is /opt/ai-ops-runner on Linux"
  fi
  if grep -q "flock" "$SHIP" && grep -q "ship.lock" "$SHIP"; then
    run true "ship_pipeline uses flock on ship.lock"
  else
    run false "ship_pipeline must use flock .locks/ship.lock"
  fi
else
  run false "ship_pipeline.sh not found"
fi

# --- Deploy pipeline: no git push / gh auth in deploy steps (guard that checks script is OK) ---
DEPLOY="$OPS_DIR/deploy_pipeline.sh"
if [ -f "$DEPLOY" ]; then
  # Lines that are the guard (grep of this script) or comments are OK
  BAD_LINES=0
  while IFS= read -r line; do
    echo "$line" | grep -q "git push\|gh auth" || continue
    echo "$line" | grep -q "^[[:space:]]*#" && continue
    echo "$line" | grep -q "must not\|forbid\|guard\|no push\|No git push\|GUARD_FAIL\|deploy_pipeline.sh\|deploy_pipeline\|push_capability\|push credentials\|assert_pull_only\|script_contains_push\|grep -q\|grep -n\|VIOLATIONS\|write_fail\|error_class\|next_auto_fix" && continue
    BAD_LINES=$((BAD_LINES + 1))
  done < "$DEPLOY"
  if [ "$BAD_LINES" -eq 0 ]; then
    run true "deploy_pipeline has no executable push/gh auth"
  else
    run false "deploy_pipeline must not run git push or gh auth"
  fi
  if grep -q "flock" "$DEPLOY" && grep -q "deploy.lock" "$DEPLOY"; then
    run true "deploy_pipeline uses flock on deploy.lock"
  else
    run false "deploy_pipeline must use flock .locks/deploy.lock"
  fi
  if grep -q "assert_production_pull_only" "$DEPLOY"; then
    run true "deploy_pipeline runs assert_production_pull_only"
  else
    run false "deploy_pipeline should run assert_production_pull_only"
  fi
else
  run false "deploy_pipeline.sh not found"
fi

# --- assert_production_pull_only exists ---
ASSERT="$OPS_DIR/assert_production_pull_only.sh"
if [ -f "$ASSERT" ] && [ -x "$ASSERT" ]; then
  run true "assert_production_pull_only.sh exists and executable"
else
  run false "assert_production_pull_only.sh must exist"
fi

# --- verify_production: enforce last_deploy_timestamp non-null after deploy ---
VERIFY="$OPS_DIR/verify_production.sh"
if [ -f "$VERIFY" ]; then
  if grep -q "last_deploy_timestamp" "$VERIFY" && grep -q "missing.*deploy" "$VERIFY"; then
    run true "verify_production enforces last_deploy_timestamp after deploy"
  else
    run false "verify_production should fail when last_deploy_timestamp missing"
  fi
  if grep -q "retry\|retries" "$VERIFY" || grep -q "curl_with_retries\|CURL_OPTS" "$VERIFY"; then
    run true "verify_production uses retries/timeouts for curl"
  else
    run false "verify_production should use curl retries/timeouts"
  fi
else
  run false "verify_production.sh not found"
fi

echo ""
echo "=== pipeline_guards_selftest: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0

#!/usr/bin/env bash
# deploy_verify_aiops1.sh — Full deploy+verify runbook for aiops-1. Produces final DELIVERABLES block.
# Run on aiops-1 from repo root (e.g. /opt/ai-ops-runner). No secrets printed.
# Usage: cd /opt/ai-ops-runner && sudo ./ops/deploy_verify_aiops1.sh
# Or:    REPO_ROOT=/opt/ai-ops-runner ./ops/deploy_verify_aiops1.sh
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/opt/ai-ops-runner}"
EXPECTED_MAIN_SHA="${EXPECTED_MAIN_SHA:-ef79dd92ff73486126eccce936669e70b743393b}"
BASE="${OPENCLAW_HQ_BASE:-http://127.0.0.1:8787}"

# Outputs collected for DELIVERABLES
DELIV_ORIGIN_MAIN_SHA=""
DELIV_DEPLOYED_SHA=""
DELIV_HOST_EXECUTOR_REACHABLE=""
DELIV_HOST_EXECUTOR_STATUS_JSON=""
DELIV_APPLY_RUN_ID=""
DELIV_APPLY_STATUS=""
DELIV_APPLY_EXIT_CODE=""
DELIV_APPLY_ERROR_SUMMARY=""
DELIV_APPLY_ARTIFACT_LINK=""
DELIV_APPLY_STDERR_EXISTS=""
DELIV_WATCHDOG_TIMER_ACTIVE=""
DELIV_WATCHDOG_ENABLED=""
DELIV_WATCHDOG_LAST_LOG=""

cd "$REPO_ROOT"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR" && pwd)"

echo "=== STEP 0 — Identify environment (aiops-1) ==="
hostname
date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S%z
whoami
git rev-parse --show-toplevel
git remote -v

echo ""
echo "=== STEP 1 — Sync to origin/main (no local edits) ==="
git fetch origin main
git reset --hard origin/main
DELIV_ORIGIN_MAIN_SHA="$(git rev-parse HEAD)"
DELIV_DEPLOYED_SHA="$DELIV_ORIGIN_MAIN_SHA"
git rev-parse HEAD
git rev-parse --short HEAD
if [ "$DELIV_ORIGIN_MAIN_SHA" != "$EXPECTED_MAIN_SHA" ]; then
  echo "WARNING: origin/main SHA differs from expected. Expected: $EXPECTED_MAIN_SHA Got: $DELIV_ORIGIN_MAIN_SHA"
fi

echo ""
echo "=== STEP 2 — Deploy stack (until green preferred) ==="
DEPLOY_EXIT=0
DEPLOY_RUN_ID=""
if [ -f "$OPS_DIR/deploy_until_green.sh" ]; then
  ./ops/deploy_until_green.sh || DEPLOY_EXIT=$?
  # Try to get run_id from latest deploy artifact
  if [ -d "$REPO_ROOT/artifacts/deploy" ]; then
    LATEST="$(ls -t "$REPO_ROOT/artifacts/deploy" 2>/dev/null | head -1)"
    if [ -n "$LATEST" ] && [ -f "$REPO_ROOT/artifacts/deploy/$LATEST/deploy_result.json" ]; then
      DEPLOY_RUN_ID="$LATEST"
    fi
  fi
else
  ./ops/deploy_pipeline.sh || DEPLOY_EXIT=$?
  if [ -d "$REPO_ROOT/artifacts/deploy" ]; then
    LATEST="$(ls -t "$REPO_ROOT/artifacts/deploy" 2>/dev/null | head -1)"
    DEPLOY_RUN_ID="${LATEST:-}"
  fi
fi
echo "Deploy exit code: $DEPLOY_EXIT  run_id: ${DEPLOY_RUN_ID:-—}"

echo ""
echo "=== STEP 3 — hostd + watchdog installed/enabled (idempotent) ==="
sudo ./ops/install_openclaw_hostd.sh || true
sudo systemctl is-active openclaw-hostd 2>/dev/null || true
sudo systemctl is-enabled openclaw-hostd 2>/dev/null || true
DELIV_WATCHDOG_TIMER_ACTIVE="$(sudo systemctl is-active openclaw-executor-watchdog.timer 2>/dev/null || echo "unknown")"
DELIV_WATCHDOG_ENABLED="$(sudo systemctl is-enabled openclaw-executor-watchdog.timer 2>/dev/null || echo "unknown")"
sudo systemctl is-active openclaw-executor-watchdog.timer 2>/dev/null || true
sudo systemctl is-enabled openclaw-executor-watchdog.timer 2>/dev/null || true
sudo systemctl status openclaw-executor-watchdog.timer --no-pager 2>/dev/null || true
sudo tail -40 /var/lib/ai-ops-runner/executor_watchdog/watchdog.log 2>/dev/null || true
DELIV_WATCHDOG_LAST_LOG="$(sudo tail -15 /var/lib/ai-ops-runner/executor_watchdog/watchdog.log 2>/dev/null | sed 's/^/  /' || echo "  (no log)")"

echo ""
echo "=== STEP 4 — host-level hostd health ==="
HOSTD_HEALTH=""
if curl -fsS http://127.0.0.1:8877/health >/dev/null 2>&1; then
  HOSTD_HEALTH="OK"
  curl -fsS http://127.0.0.1:8877/health && echo " HOSTD_HOST_HEALTH_OK"
else
  echo "HOSTD_HOST_HEALTH_FAIL"
  sudo ss -ltnp | egrep -i ':8877|hostd|openclaw' || true
  sudo journalctl -u openclaw-hostd -n 200 --no-pager 2>/dev/null || true
  sudo systemctl restart openclaw-hostd
  sleep 2
  if curl -fsS http://127.0.0.1:8877/health >/dev/null 2>&1; then
    HOSTD_HEALTH="OK"
    echo "After restart: HOSTD_HOST_HEALTH_OK"
  else
    echo "After restart: HOSTD_HOST_HEALTH_FAIL — last 200 journal lines above"
  fi
fi
sudo ss -ltnp | egrep -i ':8877|hostd|openclaw' || true

echo ""
echo "=== STEP 5 — Verify HQ endpoints ==="
curl -sS "$BASE/api/ui/health_public" | jq '{ ok, build_sha, server_time, artifacts: .artifacts }' 2>/dev/null || true
AUTH_STATUS="$(curl -sS "$BASE/api/auth/status" 2>/dev/null || echo '{}')"
echo "$AUTH_STATUS" | jq '{ ok, host_executor_reachable, admin_token_loaded, trust_tailscale, notes }' 2>/dev/null || true
DELIV_HOST_EXECUTOR_REACHABLE="$(echo "$AUTH_STATUS" | jq -r '.host_executor_reachable // "unknown"')"
HOST_EXEC_STATUS="$(curl -sS "$BASE/api/host-executor/status" 2>/dev/null || echo '{}')"
echo "$HOST_EXEC_STATUS" | jq '{ ok, console_can_reach_hostd, console_network_mode, executor_url, last_success_at, last_failure_at, error_class, message_redacted }' 2>/dev/null || true
DELIV_HOST_EXECUTOR_STATUS_JSON="$HOST_EXEC_STATUS"
curl -sS -i "$BASE/api/autopilot/status" 2>/dev/null | head -n 25 || true

# If host_executor_reachable=false: collect diagnostics, minimal fix, re-check until true (bounded)
ATTEMPTS=0
MAX_AUTH_ATTEMPTS=3
while [ "$DELIV_HOST_EXECUTOR_REACHABLE" != "true" ] && [ "$ATTEMPTS" -lt "$MAX_AUTH_ATTEMPTS" ]; do
  echo "host_executor_reachable=false — collecting diagnostics (attempt $((ATTEMPTS+1))/$MAX_AUTH_ATTEMPTS)"
  curl -sS "$BASE/api/host-executor/status" | jq . 2>/dev/null || true
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null || true
  CONSOLE="$(docker ps --format '{{.Names}}' 2>/dev/null | grep -iE 'console|openclaw|hq' | head -n1)"
  echo "CONSOLE=$CONSOLE"
  if [ -n "$CONSOLE" ]; then
    docker inspect "$CONSOLE" --format '{{.HostConfig.NetworkMode}}' 2>/dev/null || true
    docker inspect "$CONSOLE" --format '{{json .Config.Env}}' 2>/dev/null | jq -r '.[]' 2>/dev/null | egrep -i 'OPENCLAW_HOSTD_URL|HOSTD|EXECUTOR' || true
    docker exec "$CONSOLE" sh -lc 'curl -fsS http://127.0.0.1:8877/health && echo CONSOLE_TO_HOSTD_OK || echo CONSOLE_TO_HOSTD_FAIL' 2>/dev/null || true
    sudo systemctl restart openclaw-hostd 2>/dev/null
    sleep 2
    docker restart "$CONSOLE" 2>/dev/null
    sleep 5
  fi
  AUTH_STATUS="$(curl -sS "$BASE/api/auth/status" 2>/dev/null || echo '{}')"
  DELIV_HOST_EXECUTOR_REACHABLE="$(echo "$AUTH_STATUS" | jq -r '.host_executor_reachable // "unknown"')"
  HOST_EXEC_STATUS="$(curl -sS "$BASE/api/host-executor/status" 2>/dev/null || echo '{}')"
  DELIV_HOST_EXECUTOR_STATUS_JSON="$HOST_EXEC_STATUS"
  ATTEMPTS=$((ATTEMPTS+1))
done

if [ "$DELIV_HOST_EXECUTOR_REACHABLE" != "true" ]; then
  echo "WARNING: host_executor_reachable still not true after $MAX_AUTH_ATTEMPTS attempts. Proceeding; do not click Apply from UI."
fi

echo ""
echo "=== STEP 6 — Trigger ONE Apply from HQ (no-click) ==="
APPLY_EXIT=0
APPLY_OUTPUT=""
APPLY_SCRIPT="$OPS_DIR/vps_apply_aiops1.sh"
if [ "$DELIV_HOST_EXECUTOR_REACHABLE" = "true" ]; then
  if [ ! -x "$APPLY_SCRIPT" ]; then
    echo "ERROR: Apply script missing or not executable: $APPLY_SCRIPT (fail-closed)" >&2
    exit 1
  fi
  APPLY_OUTPUT="$(OPENCLAW_HQ_BASE="$BASE" "$APPLY_SCRIPT" 2>&1)" || APPLY_EXIT=$?
  echo "$APPLY_OUTPUT"
  DELIV_APPLY_EXIT_CODE="$APPLY_EXIT"
  DELIV_APPLY_RUN_ID="$(echo "$APPLY_OUTPUT" | sed -n 's/run_id: *//p' | head -1)"
  DELIV_APPLY_STATUS="$(echo "$APPLY_OUTPUT" | sed -n 's/status: *//p' | head -1)"
  [ -z "$DELIV_APPLY_STATUS" ] && DELIV_APPLY_STATUS="vps_apply_exit_${APPLY_EXIT}"
  DELIV_APPLY_ERROR_SUMMARY="$(echo "$APPLY_OUTPUT" | sed -n 's/error_summary: *//p' | head -1)"
  DELIV_APPLY_ARTIFACT_LINK="$(echo "$APPLY_OUTPUT" | sed -n 's/artifacts_link: *//p' | head -1)"
  if [ -n "$DELIV_APPLY_RUN_ID" ] && [ "$DELIV_APPLY_RUN_ID" != "—" ]; then
    curl -sS "$BASE/api/runs?id=$DELIV_APPLY_RUN_ID" | jq . 2>/dev/null || true
  fi
  if [ "$APPLY_EXIT" -ne 0 ] && [ -n "$DELIV_APPLY_ARTIFACT_LINK" ] && [ "$DELIV_APPLY_ARTIFACT_LINK" != "—" ]; then
    ARTIFACT_DIR="$(echo "$APPLY_OUTPUT" | sed -n 's/artifact_dir: *//p' | head -1)"
    if [ -n "$ARTIFACT_DIR" ]; then
      STDR="$(curl -sS "$BASE/api/artifacts/browse?path=${ARTIFACT_DIR}/stderr.txt" 2>/dev/null | jq -r '.content // .error' 2>/dev/null)"
      if [ -n "$STDR" ]; then DELIV_APPLY_STDERR_EXISTS="yes"; else DELIV_APPLY_STDERR_EXISTS="no"; fi
    fi
  fi
else
  DELIV_APPLY_RUN_ID="— (skipped: host_executor_reachable not true)"
  DELIV_APPLY_STATUS="skipped"
  DELIV_APPLY_EXIT_CODE="—"
  DELIV_APPLY_ERROR_SUMMARY="host_executor_reachable=false"
  DELIV_APPLY_ARTIFACT_LINK="—"
  DELIV_APPLY_STDERR_EXISTS="—"
fi

# Normalize for DELIVERABLES
[ -z "$DELIV_APPLY_EXIT_CODE" ] && DELIV_APPLY_EXIT_CODE="—"
[ -z "$DELIV_APPLY_ERROR_SUMMARY" ] && DELIV_APPLY_ERROR_SUMMARY="(none)"
[ -z "$DELIV_APPLY_ARTIFACT_LINK" ] && DELIV_APPLY_ARTIFACT_LINK="—"
[ -z "$DELIV_APPLY_STDERR_EXISTS" ] && DELIV_APPLY_STDERR_EXISTS="—"

echo ""
echo "=== STEP 7 — DELIVERABLES (exact block) ==="
echo "--- DELIVERABLES ---"
echo "- origin/main SHA: $DELIV_ORIGIN_MAIN_SHA"
echo "- aiops-1 deployed SHA: $DELIV_DEPLOYED_SHA"
echo "- /api/auth/status host_executor_reachable: $DELIV_HOST_EXECUTOR_REACHABLE"
echo "- /api/host-executor/status (key fields):"
echo "$DELIV_HOST_EXECUTOR_STATUS_JSON" | jq -c '{ ok, console_can_reach_hostd, executor_url, last_success_at, last_failure_at }' 2>/dev/null || echo "  (raw) $DELIV_HOST_EXECUTOR_STATUS_JSON"
echo "- apply run_id: $DELIV_APPLY_RUN_ID"
echo "- apply status + exit_code: $DELIV_APPLY_STATUS + $DELIV_APPLY_EXIT_CODE"
echo "- apply error_summary (if any): $DELIV_APPLY_ERROR_SUMMARY"
echo "- apply artifact link + stderr.txt exists when failing: $DELIV_APPLY_ARTIFACT_LINK  stderr_exists=$DELIV_APPLY_STDERR_EXISTS"
echo "- watchdog status (timer active + last log lines): timer_active=$DELIV_WATCHDOG_TIMER_ACTIVE  enabled=$DELIV_WATCHDOG_ENABLED"
echo "$DELIV_WATCHDOG_LAST_LOG"
echo "--- END DELIVERABLES ---"

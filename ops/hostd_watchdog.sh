#!/usr/bin/env bash
# hostd_watchdog.sh — Idempotent hostd health probe. Restarts openclaw-hostd if health fails.
# Logs to journald via logger (tag: openclaw-hostd-watchdog). No secrets printed.
# Writes artifacts/hostd_guard/<run_id>/status.json for HQ Audit visibility.
# Run by openclaw-hostd-watchdog.timer every 60s.
set -euo pipefail

HOSTD_URL="${HOSTD_HEALTH_URL:-http://127.0.0.1:8877/health}"
TAG="openclaw-hostd-watchdog"
ROOT_DIR="${OPENCLAW_REPO_ROOT:-/opt/ai-ops-runner}"
RUN_ID="$(date -u +%Y%m%d_%H%M%S)_hostd_guard"
ART_DIR="$ROOT_DIR/artifacts/hostd_guard/$RUN_ID"

log() {
  logger -t "$TAG" "$*"
}

write_status() {
  local ok="$1"
  local action="${2:-probe}"
  mkdir -p "$ART_DIR"
  printf '%s\n' "{\"run_id\":\"$RUN_ID\",\"ok\":$ok,\"action\":\"$action\",\"timestamp_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" >"$ART_DIR/status.json"
}

if curl -fsS --max-time 2 "$HOSTD_URL" >/dev/null 2>&1; then
  write_status true "probe"
  exit 0
fi

log "hostd health FAIL — restarting openclaw-hostd"
write_status false "restarting"
systemctl restart openclaw-hostd.service 2>/dev/null || true
sleep 2

if curl -fsS --max-time 2 "$HOSTD_URL" >/dev/null 2>&1; then
  log "hostd health OK after restart"
  write_status true "restarted"
  exit 0
fi

log "hostd still FAIL after restart — check journalctl -u openclaw-hostd"
write_status false "restart_failed"
exit 0

#!/usr/bin/env bash
# hostd_watchdog.sh — Idempotent hostd health probe. Restarts openclaw-hostd if health fails.
# Logs to journald via logger (tag: openclaw-hostd-watchdog). No secrets printed.
# Run by openclaw-hostd-watchdog.timer every 60s.
set -euo pipefail

HOSTD_URL="${HOSTD_HEALTH_URL:-http://127.0.0.1:8877/health}"
TAG="openclaw-hostd-watchdog"

log() {
  logger -t "$TAG" "$*"
}

if curl -fsS --max-time 2 "$HOSTD_URL" >/dev/null 2>&1; then
  exit 0
fi

log "hostd health FAIL — restarting openclaw-hostd"
systemctl restart openclaw-hostd.service 2>/dev/null || true
sleep 2

if curl -fsS --max-time 2 "$HOSTD_URL" >/dev/null 2>&1; then
  log "hostd health OK after restart"
  exit 0
fi

log "hostd still FAIL after restart — check journalctl -u openclaw-hostd"
exit 0

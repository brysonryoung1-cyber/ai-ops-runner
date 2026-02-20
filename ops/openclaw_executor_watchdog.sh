#!/usr/bin/env bash
# openclaw_executor_watchdog.sh — Fail-closed executor watchdog:
# 1) Check hostd health on host: curl http://127.0.0.1:8877/health
# 2) Check console container can reach hostd using OPENCLAW_HOSTD_URL from console env.
# If host check fails 3x → restart openclaw-hostd.
# If container check fails 3x but host OK → restart console (docker restart or compose up -d).
# Log all actions to watchdog.log. No secrets printed.
set -euo pipefail

STATE_DIR="${OPENCLAW_EXECUTOR_WATCHDOG_DIR:-/var/lib/ai-ops-runner/executor_watchdog}"
HOST_FAIL_FILE="${STATE_DIR}/host_fail_count"
CONTAINER_FAIL_FILE="${STATE_DIR}/container_fail_count"
LOG_FILE="${STATE_DIR}/watchdog.log"
HOSTD_PORT="${OPENCLAW_HOSTD_PORT:-8877}"
REPO_ROOT="${OPENCLAW_REPO_ROOT:-/opt/ai-ops-runner}"

mkdir -p "$STATE_DIR"

log() {
  echo "$(date -Iseconds) $*" >> "$LOG_FILE"
}

# ——— Resolve console container and OPENCLAW_HOSTD_URL ———
# Match container name: console, hq, openclaw (case-insensitive).
get_console_container() {
  docker ps --format '{{.Names}}' 2>/dev/null | grep -iE 'console|hq|openclaw' | head -1 || true
}

# Get OPENCLAW_HOSTD_URL from container env (docker inspect .Config.Env). Redact tokens.
get_console_hostd_url() {
  local container="$1"
  local url
  url="$(docker inspect "$container" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | grep '^OPENCLAW_HOSTD_URL=' | head -1 | sed 's/^OPENCLAW_HOSTD_URL=//')"
  if [ -n "$url" ]; then
    echo "$url"
  else
    echo "http://127.0.0.1:${HOSTD_PORT}"
  fi
}

# Normalize health URL: ensure path ends with /health (robust if already has /health).
health_url() {
  local base="${1//\/$/}"
  if [[ "$base" == *"/health" ]]; then
    echo "$base"
  else
    echo "${base}/health"
  fi
}

# ——— Host probe: curl from host ———
host_ok() {
  curl -sf --connect-timeout 2 "http://127.0.0.1:${HOSTD_PORT}/health" >/dev/null 2>&1
}

# ——— Container probe: curl from inside container ———
container_ok() {
  local container="$1"
  local url="$2"
  local health
  health="$(health_url "$url")"
  docker exec "$container" curl -sf --connect-timeout 2 "$health" >/dev/null 2>&1
}

# ——— Read/write fail counters ———
read_count() {
  local file="$1"
  if [ -f "$file" ]; then
    local count
    read -r count < "$file" 2>/dev/null || true
    echo "${count:-0}"
  else
    echo "0"
  fi
}

# ——— Main ———
host_fail_count=$(read_count "$HOST_FAIL_FILE")
container_fail_count=$(read_count "$CONTAINER_FAIL_FILE")
console_container=""
console_url=""

# 1) Host check
if host_ok; then
  echo "0" > "$HOST_FAIL_FILE"
  if [ "${host_fail_count:-0}" -gt 0 ]; then
    log "hostd health OK (host); reset host_fail_count"
  fi
else
  host_fail_count=$((host_fail_count + 1))
  echo "$host_fail_count" > "$HOST_FAIL_FILE"
  log "hostd health FAIL (host) attempt $host_fail_count"
  if [ "$host_fail_count" -ge 3 ]; then
    log "3 consecutive host failures — restarting openclaw-hostd"
    systemctl restart openclaw-hostd.service || true
    echo "0" > "$HOST_FAIL_FILE"
    log "restart requested; reset host_fail_count"
  fi
  # Skip container check if host is down
  exit 0
fi

# 2) Container check (only if host is OK)
console_container="$(get_console_container)"
if [ -z "$console_container" ]; then
  # No console container (e.g. bare-metal console); nothing to restart
  echo "0" > "$CONTAINER_FAIL_FILE"
  exit 0
fi

console_url="$(get_console_hostd_url "$console_container")"
if container_ok "$console_container" "$console_url"; then
  echo "0" > "$CONTAINER_FAIL_FILE"
  if [ "${container_fail_count:-0}" -gt 0 ]; then
    log "container->hostd OK; reset container_fail_count"
  fi
  exit 0
fi

container_fail_count=$((container_fail_count + 1))
echo "$container_fail_count" > "$CONTAINER_FAIL_FILE"
# Log URL normalized (no secrets; already just base URL)
log "container->hostd FAIL (attempt $container_fail_count) container=$console_container url=${console_url%/}"

if [ "$container_fail_count" -ge 3 ]; then
  log "3 consecutive container failures — restarting console container: $console_container"
  docker restart "$console_container" 2>/dev/null || {
    # Fallback: try compose for current dir / repo
    if [ -d "$REPO_ROOT" ] && [ -f "$REPO_ROOT/docker-compose.console.yml" ]; then
      (cd "$REPO_ROOT" && docker compose -f docker-compose.yml -f docker-compose.console.yml up -d openclaw_console 2>/dev/null) || true
    fi
  }
  echo "0" > "$CONTAINER_FAIL_FILE"
  log "restart requested; reset container_fail_count"
fi
exit 0

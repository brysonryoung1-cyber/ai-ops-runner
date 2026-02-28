#!/usr/bin/env bash
# rootd_watchdog.sh — Idempotent rootd health probe. Restarts openclaw-rootd if socket missing or unhealthy.
# Run by openclaw-rootd-watchdog.timer every 60s.
set -euo pipefail

TAG="openclaw-rootd-watchdog"
ROOT_DIR="${OPENCLAW_REPO_ROOT:-/opt/ai-ops-runner}"
SOCKET="/run/openclaw/rootd.sock"
RUN_ID="$(date -u +%Y%m%d_%H%M%S)_rootd_guard"
ART_DIR="$ROOT_DIR/artifacts/system/rootd_watchdog/$RUN_ID"

log() {
  logger -t "$TAG" "$*"
}

write_status() {
  local ok="$1"
  local action="${2:-probe}"
  mkdir -p "$ART_DIR"
  printf '%s\n' "{\"run_id\":\"$RUN_ID\",\"ok\":$ok,\"action\":\"$action\",\"timestamp_utc\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" >"$ART_DIR/status.json"
}

# Check socket exists
if [ ! -S "$SOCKET" ]; then
  log "rootd socket missing at $SOCKET — restarting openclaw-rootd"
  write_status false "socket_missing"
  systemctl restart openclaw-rootd.service 2>/dev/null || true
  sleep 2
  if [ -S "$SOCKET" ]; then
    log "rootd socket restored after restart"
    write_status true "restarted"
    exit 0
  fi
  log "rootd socket still missing after restart"
  write_status false "restart_failed"
  exit 0
fi

# Health probe via Unix socket
if python3 -c "
import socket, json
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(3)
s.connect('$SOCKET')
s.sendall(b'GET /health HTTP/1.0\r\nHost: rootd\r\n\r\n')
data = s.recv(4096).decode()
s.close()
body = data.split('\r\n\r\n', 1)[-1]
d = json.loads(body)
exit(0 if d.get('ok') else 1)
" 2>/dev/null; then
  write_status true "probe"
  exit 0
fi

log "rootd health FAIL — restarting openclaw-rootd"
write_status false "restarting"
systemctl restart openclaw-rootd.service 2>/dev/null || true
sleep 2

if python3 -c "
import socket, json
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(3)
s.connect('$SOCKET')
s.sendall(b'GET /health HTTP/1.0\r\nHost: rootd\r\n\r\n')
data = s.recv(4096).decode()
s.close()
body = data.split('\r\n\r\n', 1)[-1]
d = json.loads(body)
exit(0 if d.get('ok') else 1)
" 2>/dev/null; then
  log "rootd health OK after restart"
  write_status true "restarted"
  exit 0
fi

log "rootd still FAIL after restart — check journalctl -u openclaw-rootd"
write_status false "restart_failed"
exit 0

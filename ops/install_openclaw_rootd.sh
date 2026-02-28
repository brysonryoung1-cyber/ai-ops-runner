#!/usr/bin/env bash
# install_openclaw_rootd.sh — Idempotent systemd install for openclaw-rootd.
# Must run as root. Creates HMAC key if missing, installs service, starts rootd.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
UNIT_PATH="/etc/systemd/system/openclaw-rootd.service"
HMAC_KEY_PATH="/etc/ai-ops-runner/secrets/rootd_hmac_key"
SOCKET_DIR="/run/openclaw"

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: install_openclaw_rootd.sh must run as root" >&2
  exit 1
fi

# Ensure secrets directory
mkdir -p /etc/ai-ops-runner/secrets
chmod 700 /etc/ai-ops-runner/secrets

# Generate HMAC key if missing
if [ ! -f "$HMAC_KEY_PATH" ]; then
  openssl rand -hex 32 > "$HMAC_KEY_PATH"
  chmod 600 "$HMAC_KEY_PATH"
  chown root:root "$HMAC_KEY_PATH"
  echo "rootd: generated HMAC key at $HMAC_KEY_PATH"
else
  echo "rootd: HMAC key exists at $HMAC_KEY_PATH"
fi

# Ensure socket directory
mkdir -p "$SOCKET_DIR"
chmod 755 "$SOCKET_DIR"

# Install systemd service (substitute ROOT_DIR)
sed "s|/opt/ai-ops-runner|$ROOT_DIR|g" "$ROOT_DIR/ops/systemd/openclaw-rootd.service" > "$UNIT_PATH"

systemctl daemon-reload
systemctl enable openclaw-rootd.service
systemctl restart openclaw-rootd.service
echo "openclaw-rootd: installed and restarted"

# Health check
sleep 1
if python3 -c "
import socket, json
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(3)
s.connect('/run/openclaw/rootd.sock')
s.sendall(b'GET /health HTTP/1.0\r\nHost: rootd\r\n\r\n')
data = s.recv(4096).decode()
s.close()
body = data.split('\r\n\r\n', 1)[-1]
d = json.loads(body)
exit(0 if d.get('ok') else 1)
" 2>/dev/null; then
  echo "openclaw-rootd: health OK"
else
  echo "WARNING: rootd health check failed" >&2
  journalctl -u openclaw-rootd.service -n 20 --no-pager >&2 || true
  exit 1
fi

# Install rootd watchdog timer
WATCHDOG_SERVICE="$ROOT_DIR/ops/systemd/openclaw-rootd-watchdog.service"
WATCHDOG_TIMER="$ROOT_DIR/ops/systemd/openclaw-rootd-watchdog.timer"
if [ -f "$WATCHDOG_SERVICE" ] && [ -f "$WATCHDOG_TIMER" ]; then
  sed "s|/opt/ai-ops-runner|$ROOT_DIR|g" "$WATCHDOG_SERVICE" > /etc/systemd/system/openclaw-rootd-watchdog.service
  cp "$WATCHDOG_TIMER" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now openclaw-rootd-watchdog.timer
  echo "openclaw-rootd-watchdog: timer enabled"
fi

# Install serve enforcer timer
ENFORCER_SERVICE="$ROOT_DIR/ops/systemd/openclaw-serve-enforcer.service"
ENFORCER_TIMER="$ROOT_DIR/ops/systemd/openclaw-serve-enforcer.timer"
if [ -f "$ENFORCER_SERVICE" ] && [ -f "$ENFORCER_TIMER" ]; then
  sed "s|/opt/ai-ops-runner|$ROOT_DIR|g" "$ENFORCER_SERVICE" > /etc/systemd/system/openclaw-serve-enforcer.service
  cp "$ENFORCER_TIMER" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now openclaw-serve-enforcer.timer
  echo "openclaw-serve-enforcer: timer enabled"
fi

echo "rootd install complete"

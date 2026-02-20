#!/usr/bin/env bash
# install_openclaw_hostd.sh — Idempotent systemd install for openclaw-hostd.
# Run from repo root. Creates /etc/systemd/system/openclaw-hostd.service.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="${PYTHON:-/usr/bin/python3}"
UNIT_PATH="/etc/systemd/system/openclaw-hostd.service"

if [ ! -f "$ROOT_DIR/ops/openclaw_hostd.py" ]; then
  echo "ERROR: openclaw_hostd.py not found under $ROOT_DIR" >&2
  exit 1
fi

# Ensure hostd env has OPENCLAW_VPS_SSH_IDENTITY when deploy key exists (Apply SSH to self)
SECRETS_DIR="/etc/ai-ops-runner/secrets"
DEPLOY_KEY="${SECRETS_DIR}/openclaw_ssh/vps_deploy_ed25519"
HOSTD_ENV="${SECRETS_DIR}/openclaw_hostd.env"
if [ -f "$DEPLOY_KEY" ]; then
  sudo mkdir -p "$SECRETS_DIR"
  if ! sudo grep -q "OPENCLAW_VPS_SSH_IDENTITY" "$HOSTD_ENV" 2>/dev/null; then
    echo "OPENCLAW_VPS_SSH_IDENTITY=$DEPLOY_KEY" | sudo tee -a "$HOSTD_ENV" >/dev/null
    echo "  Set OPENCLAW_VPS_SSH_IDENTITY in $HOSTD_ENV for Apply (SSH to self)"
  fi
fi

sudo tee "$UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=OpenClaw host executor (no SSH)
After=network.target

[Service]
Type=simple
EnvironmentFile=-/etc/ai-ops-runner/secrets/openclaw_hostd.env
ExecStart=$PYTHON $ROOT_DIR/ops/openclaw_hostd.py
WorkingDirectory=$ROOT_DIR
Restart=always
RestartSec=1
TimeoutStartSec=10
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable openclaw-hostd.service
sudo systemctl restart openclaw-hostd.service
echo "openclaw-hostd: installed and restarted"

# Quick health check
sleep 1
if curl -sSf --connect-timeout 2 "http://127.0.0.1:8877/health" >/dev/null; then
  echo "openclaw-hostd: health OK"
else
  echo "WARNING: hostd health check failed (curl 127.0.0.1:8877/health)" >&2
  exit 1
fi

# Optional: install hostd watchdog (probe every 3 min; restart hostd after 3 consecutive failures)
WATCHDOG_SERVICE="$ROOT_DIR/ops/systemd/openclaw-hostd-watchdog.service"
WATCHDOG_TIMER="$ROOT_DIR/ops/systemd/openclaw-hostd-watchdog.timer"
if [ -f "$WATCHDOG_SERVICE" ] && [ -f "$WATCHDOG_TIMER" ]; then
  sed "s|/opt/ai-ops-runner|$ROOT_DIR|g" "$WATCHDOG_SERVICE" | sudo tee /etc/systemd/system/openclaw-hostd-watchdog.service >/dev/null
  sudo cp "$WATCHDOG_TIMER" /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now openclaw-hostd-watchdog.timer
  echo "openclaw-hostd-watchdog: timer enabled (repo $ROOT_DIR)"
fi

# Optional: install executor watchdog (host + console→hostd; every 2.5 min)
EXECUTOR_WATCHDOG_SERVICE="$ROOT_DIR/ops/systemd/openclaw-executor-watchdog.service"
EXECUTOR_WATCHDOG_TIMER="$ROOT_DIR/ops/systemd/openclaw-executor-watchdog.timer"
if [ -f "$EXECUTOR_WATCHDOG_SERVICE" ] && [ -f "$EXECUTOR_WATCHDOG_TIMER" ]; then
  sed "s|/opt/ai-ops-runner|$ROOT_DIR|g" "$EXECUTOR_WATCHDOG_SERVICE" | sudo tee /etc/systemd/system/openclaw-executor-watchdog.service >/dev/null
  sudo cp "$EXECUTOR_WATCHDOG_TIMER" /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now openclaw-executor-watchdog.timer
  echo "openclaw-executor-watchdog: timer enabled (repo $ROOT_DIR)"
fi

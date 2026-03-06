#!/usr/bin/env bash
# openclaw_install_state_pack.sh — Install openclaw-state-pack timer and shared env.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== openclaw_install_state_pack.sh ==="
sudo bash "$SCRIPT_DIR/openclaw_install_shared_env.sh"
sudo cp "$SCRIPT_DIR/systemd/openclaw-state-pack.service" "$SYSTEMD_DIR/"
sudo cp "$SCRIPT_DIR/systemd/openclaw-state-pack.timer" "$SYSTEMD_DIR/"
sudo systemctl daemon-reload
sudo systemctl enable openclaw-state-pack.timer
sudo systemctl start openclaw-state-pack.timer
echo "  State pack timer installed (every 5 min with jitter)"

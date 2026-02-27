#!/usr/bin/env bash
# openclaw_install_canary.sh — Install openclaw-canary timer (system.canary every 15 min).
# Idempotent. Safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_DIR="/etc/systemd/system"
echo "=== openclaw_install_canary.sh ==="
sudo cp "$SCRIPT_DIR/systemd/openclaw-canary.service" "$SYSTEMD_DIR/"
sudo cp "$SCRIPT_DIR/systemd/openclaw-canary.timer" "$SYSTEMD_DIR/"
sudo systemctl daemon-reload
sudo systemctl enable openclaw-canary.timer
sudo systemctl start openclaw-canary.timer
echo "  Canary timer installed (every 15 min with jitter)"
echo "  Run once: sudo systemctl start openclaw-canary.service"

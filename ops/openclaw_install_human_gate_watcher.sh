#!/usr/bin/env bash
# openclaw_install_human_gate_watcher.sh — Install HumanGateWatcher timer (auto-resume after Kajabi login).
# Idempotent. Safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== openclaw_install_human_gate_watcher.sh ==="
sudo cp "$SCRIPT_DIR/systemd/openclaw-human-gate-watcher.service" "$SYSTEMD_DIR/"
sudo cp "$SCRIPT_DIR/systemd/openclaw-human-gate-watcher.timer" "$SYSTEMD_DIR/"
sudo systemctl daemon-reload
sudo systemctl enable openclaw-human-gate-watcher.timer 2>/dev/null || true
sudo systemctl start openclaw-human-gate-watcher.timer 2>/dev/null || true
echo "  HumanGateWatcher timer installed (every 90s)"
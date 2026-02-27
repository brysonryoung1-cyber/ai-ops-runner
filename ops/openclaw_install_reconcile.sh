#!/usr/bin/env bash
# openclaw_install_reconcile.sh — Install openclaw-reconcile timer (system.reconcile every N min).
# Idempotent. Safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== openclaw_install_reconcile.sh ==="
sudo cp "$SCRIPT_DIR/systemd/openclaw-reconcile.service" "$SYSTEMD_DIR/"
sudo cp "$SCRIPT_DIR/systemd/openclaw-reconcile.timer" "$SYSTEMD_DIR/"
sudo systemctl daemon-reload
sudo systemctl enable openclaw-reconcile.timer
sudo systemctl start openclaw-reconcile.timer
echo "  Reconcile timer installed (every 5-10 min with jitter)"

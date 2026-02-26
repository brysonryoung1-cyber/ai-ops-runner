#!/usr/bin/env bash
# openclaw_install_soma_autopilot.sh — Install Soma Autopilot systemd timer.
#
# Timer runs every 10 minutes. The tick checks /etc/ai-ops-runner/config/soma_autopilot_enabled.txt;
# if missing, tick is a no-op.
#
# To enable: sudo touch /etc/ai-ops-runner/config/soma_autopilot_enabled.txt
# To disable: sudo rm /etc/ai-ops-runner/config/soma_autopilot_enabled.txt
#
# Idempotent. Safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== openclaw_install_soma_autopilot.sh ==="

sudo mkdir -p /etc/ai-ops-runner/config
sudo mkdir -p /var/lib/ai-ops-runner/soma_autopilot
echo "  Config dir: /etc/ai-ops-runner/config"
echo "  Enabled: create /etc/ai-ops-runner/config/soma_autopilot_enabled.txt to enable"

echo "  Installing systemd units"
sudo cp "$SCRIPT_DIR/systemd/openclaw-soma-autopilot.service" "$SYSTEMD_DIR/"
sudo cp "$SCRIPT_DIR/systemd/openclaw-soma-autopilot.timer" "$SYSTEMD_DIR/"
sudo systemctl daemon-reload

sudo systemctl enable openclaw-soma-autopilot.timer 2>/dev/null || true
sudo systemctl start openclaw-soma-autopilot.timer 2>/dev/null || true
echo "  Timer: enabled and started (every 10 min)"

echo "  Timer status:"
systemctl status openclaw-soma-autopilot.timer --no-pager 2>/dev/null || true

echo ""
echo "=== openclaw_install_soma_autopilot.sh COMPLETE ==="
echo "  To enable: sudo touch /etc/ai-ops-runner/config/soma_autopilot_enabled.txt"

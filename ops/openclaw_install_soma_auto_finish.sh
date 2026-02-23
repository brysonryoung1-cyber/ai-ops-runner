#!/usr/bin/env bash
# openclaw_install_soma_auto_finish.sh — Install the Soma Auto-Finish daily timer.
#
# Timer runs daily at 6am UTC. The service checks /etc/ai-ops-runner/config/soma_kajabi_auto_finish_enabled.txt;
# if missing, the run is a no-op. Default OFF.
#
# To enable: touch /etc/ai-ops-runner/config/soma_kajabi_auto_finish_enabled.txt
# To disable: rm /etc/ai-ops-runner/config/soma_kajabi_auto_finish_enabled.txt
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

# Ensure config dir exists
sudo mkdir -p /etc/ai-ops-runner/config

echo "=== openclaw_install_soma_auto_finish.sh ==="
echo "  Installing systemd units"
sudo cp "$SCRIPT_DIR/systemd/openclaw-soma-auto-finish.service" "$SYSTEMD_DIR/"
sudo cp "$SCRIPT_DIR/systemd/openclaw-soma-auto-finish.timer" "$SYSTEMD_DIR/"
sudo systemctl daemon-reload

# Timer is installed but NOT enabled by default
sudo systemctl enable openclaw-soma-auto-finish.timer
sudo systemctl start openclaw-soma-auto-finish.timer 2>/dev/null || true

echo "  Timer: openclaw-soma-auto-finish.timer (daily 6am UTC)"
echo "  Enabled: create /etc/ai-ops-runner/config/soma_kajabi_auto_finish_enabled.txt to enable"
echo ""
echo "=== openclaw_install_soma_auto_finish.sh COMPLETE ==="

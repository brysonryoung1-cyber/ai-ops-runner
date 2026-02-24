#!/usr/bin/env bash
# install_openclaw_soma_kajabi_warm.sh — Install Soma Kajabi Session Warm timer (OFF by default).
#
# Timer runs every 6 hours. Only runs when /etc/ai-ops-runner/config/soma_kajabi_session_warm_enabled.txt exists.
# Fail-closed: if exit node offline, writes SKIPPED_EXIT_NODE_OFFLINE and does nothing.
#
# To enable: touch /etc/ai-ops-runner/config/soma_kajabi_session_warm_enabled.txt
#           sudo systemctl enable --now openclaw-soma-kajabi-warm.timer
# To disable: rm /etc/ai-ops-runner/config/soma_kajabi_session_warm_enabled.txt
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

sudo mkdir -p /etc/ai-ops-runner/config

echo "=== install_openclaw_soma_kajabi_warm.sh ==="
sed "s|/opt/ai-ops-runner|$ROOT_DIR|g" "$SCRIPT_DIR/systemd/openclaw-soma-kajabi-warm.service" | sudo tee "$SYSTEMD_DIR/openclaw-soma-kajabi-warm.service" >/dev/null
sudo cp "$SCRIPT_DIR/systemd/openclaw-soma-kajabi-warm.timer" "$SYSTEMD_DIR/"
sudo systemctl daemon-reload

# Timer installed but NOT enabled by default
echo "  openclaw-soma-kajabi-warm: installed (disabled by default)"
echo "  To enable: touch /etc/ai-ops-runner/config/soma_kajabi_session_warm_enabled.txt && sudo systemctl enable --now openclaw-soma-kajabi-warm.timer"
echo ""

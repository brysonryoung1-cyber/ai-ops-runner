#!/usr/bin/env bash
# openclaw_install_soma_autopilot.sh — Install Soma Project Autopilot timer.
#
# Timer cadence: every 30 minutes (+ randomized delay).
# Entry command: ops/system/project_autopilot.py (Doctor-gated, fail-closed).
#
# Idempotent; safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== openclaw_install_soma_autopilot.sh ==="

sudo mkdir -p /var/lib/ai-ops-runner/soma_autopilot
echo "  State dir: /var/lib/ai-ops-runner/soma_autopilot"

echo "  Installing systemd units"
sudo cp "$SCRIPT_DIR/systemd/openclaw-soma-autopilot.service" "$SYSTEMD_DIR/"
sudo cp "$SCRIPT_DIR/systemd/openclaw-soma-autopilot.timer" "$SYSTEMD_DIR/"
sudo systemctl daemon-reload

sudo systemctl enable openclaw-soma-autopilot.timer 2>/dev/null || true
sudo systemctl start openclaw-soma-autopilot.timer 2>/dev/null || true
echo "  Timer: enabled and started (every 30 min + randomized delay)"

echo "  Timer status:"
systemctl status openclaw-soma-autopilot.timer --no-pager 2>/dev/null || true

# HumanGateWatcher: auto-resume after Kajabi login (no manual "Click Resume")
if [ -f "$SCRIPT_DIR/openclaw_install_human_gate_watcher.sh" ]; then
  bash "$SCRIPT_DIR/openclaw_install_human_gate_watcher.sh" 2>/dev/null || true
fi

echo ""
echo "=== openclaw_install_soma_autopilot.sh COMPLETE ==="
echo "  Verify: systemctl status openclaw-soma-autopilot.timer --no-pager"

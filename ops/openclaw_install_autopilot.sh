#!/usr/bin/env bash
# openclaw_install_autopilot.sh — Install or repair the openclaw-autopilot systemd timer.
# Idempotent. Safe to re-run. Creates state directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

STATE_DIR="/var/lib/ai-ops-runner/autopilot"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== openclaw_install_autopilot.sh ==="

# --- Create state directory ---
echo "  Creating state directory: $STATE_DIR"
sudo mkdir -p "$STATE_DIR"
sudo chown "$(whoami):$(id -gn)" "$STATE_DIR"

# --- Initialize state files if missing ---
[ -f "$STATE_DIR/fail_count.txt" ] || echo "0" > "$STATE_DIR/fail_count.txt"
[ -f "$STATE_DIR/last_deployed_sha.txt" ] || echo "" > "$STATE_DIR/last_deployed_sha.txt"
[ -f "$STATE_DIR/last_good_sha.txt" ] || echo "" > "$STATE_DIR/last_good_sha.txt"

# --- Seed current SHA if deploying for first time ---
if [ -z "$(cat "$STATE_DIR/last_deployed_sha.txt" 2>/dev/null)" ]; then
  CURRENT="$(cd "$ROOT_DIR" && git rev-parse HEAD 2>/dev/null || echo "")"
  if [ -n "$CURRENT" ]; then
    echo "$CURRENT" > "$STATE_DIR/last_deployed_sha.txt"
    echo "$CURRENT" > "$STATE_DIR/last_good_sha.txt"
    echo "  Seeded initial SHA: $CURRENT"
  fi
fi

# --- Copy systemd units ---
echo "  Installing systemd units"
sudo cp "$SCRIPT_DIR/systemd/openclaw-autopilot.service" "$SYSTEMD_DIR/"
sudo cp "$SCRIPT_DIR/systemd/openclaw-autopilot.timer" "$SYSTEMD_DIR/"

# --- Reload and enable ---
sudo systemctl daemon-reload
sudo systemctl enable openclaw-autopilot.timer
sudo systemctl start openclaw-autopilot.timer

echo "  Timer status:"
systemctl status openclaw-autopilot.timer --no-pager 2>/dev/null || true

echo ""
echo "=== openclaw_install_autopilot.sh COMPLETE ==="
echo "  State dir: $STATE_DIR"
echo "  Timer: openclaw-autopilot.timer (every 5 min)"
echo "  Note: Autopilot is installed but NOT enabled. Run:"
echo "    touch $STATE_DIR/enabled"
echo "  Or use HQ Settings → Autopilot → Enable"

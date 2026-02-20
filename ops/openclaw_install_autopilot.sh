#!/usr/bin/env bash
# openclaw_install_autopilot.sh — Install or repair the openclaw-autopilot systemd timer.
# Idempotent. Safe to re-run. By default: install + enable + start timer; create enabled sentinel.
#
# Flags:
#   --enable     Force create enabled sentinel and enable+start timer (default when not --disabled).
#   --disable    Remove enabled sentinel only (timer remains installed; tick is no-op without sentinel).
#   --run-now    After install/enable, run one tick immediately (systemd start or direct script).
#   --disabled   Install/repair units only; do NOT create enabled sentinel (explicit opt-out).
#
# Default (no flags): install, enable, start timer, create enabled sentinel, log AUTOPILOT_ENABLED_DEFAULT.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

STATE_DIR="/var/lib/ai-ops-runner/autopilot"
SYSTEMD_DIR="/etc/systemd/system"

# --- Parse flags ---
OPT_ENABLE=
OPT_DISABLE=
OPT_RUN_NOW=
OPT_DISABLED=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --enable)   OPT_ENABLE=1; shift ;;
    --disable)  OPT_DISABLE=1; shift ;;
    --run-now)  OPT_RUN_NOW=1; shift ;;
    --disabled) OPT_DISABLED=1; shift ;;
    -h|--help)
      echo "Usage: $0 [--enable] [--disable] [--run-now] [--disabled]"
      echo "  --enable   Force create enabled sentinel + enable+start timer"
      echo "  --disable  Remove enabled sentinel only"
      echo "  --run-now  Run one autopilot tick after install"
      echo "  --disabled Install only; do not enable (no sentinel)"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# Mutual exclusion
if [ -n "$OPT_ENABLE" ] && [ -n "$OPT_DISABLE" ]; then
  echo "ERROR: --enable and --disable are mutually exclusive." >&2
  exit 1
fi

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
sudo systemctl daemon-reload

# --- Enable/disable sentinel ---
# Default: enable (unless --disabled or explicit --disable)
SHOULD_ENABLE=
if [ -n "$OPT_DISABLE" ]; then
  rm -f "$STATE_DIR/enabled"
  echo "  Disabled: removed $STATE_DIR/enabled"
elif [ -n "$OPT_DISABLED" ]; then
  rm -f "$STATE_DIR/enabled"
  echo "  Installed only (--disabled): no enabled sentinel"
else
  # --enable or default: create sentinel and enable+start timer
  touch "$STATE_DIR/enabled"
  SHOULD_ENABLE=1
  if [ -n "$OPT_ENABLE" ]; then
    echo "  Enabled: created $STATE_DIR/enabled (--enable)"
  else
    echo "  AUTOPILOT_ENABLED_DEFAULT"
  fi
fi

# --- Enable and start timer when we created/kept enabled sentinel ---
if [ -n "$SHOULD_ENABLE" ]; then
  sudo systemctl enable --now openclaw-autopilot.timer
  echo "  Timer: enabled and started (systemctl enable --now openclaw-autopilot.timer)"
else
  echo "  Timer: installed (not started; enable via --enable or omit --disabled)"
fi

echo "  Timer status:"
systemctl status openclaw-autopilot.timer --no-pager 2>/dev/null || true

# --- Run one tick if requested ---
if [ -n "$OPT_RUN_NOW" ]; then
  echo "  Running one autopilot tick (--run-now)..."
  if command -v systemctl >/dev/null 2>&1 && systemctl is-active openclaw-autopilot.timer >/dev/null 2>&1; then
    sudo systemctl start openclaw-autopilot.service 2>&1 || true
  else
    bash "$SCRIPT_DIR/autopilot_tick.sh" 2>&1 || true
  fi
  echo "  Run-now complete."
fi

echo ""
echo "=== openclaw_install_autopilot.sh COMPLETE ==="
echo "  State dir: $STATE_DIR"
echo "  Timer: openclaw-autopilot.timer (every 5 min)"
echo "  Enabled: $([ -f "$STATE_DIR/enabled" ] && echo 'yes' || echo 'no')"

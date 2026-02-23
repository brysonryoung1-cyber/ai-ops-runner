#!/usr/bin/env bash
# soma_kajabi_auto_finish_daily.sh — Optional daily run of soma_kajabi_auto_finish.
#
# Enabled only when /etc/ai-ops-runner/config/soma_kajabi_auto_finish_enabled.txt exists.
# Default OFF. If ON and exit node offline, fail-closed (no retry beyond N times/day).
#
# Usage: Called by systemd timer (openclaw-soma-auto-finish.timer) or manually.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FLAG="/etc/ai-ops-runner/config/soma_kajabi_auto_finish_enabled.txt"
STATE_DIR="${OPENCLAW_SOMA_AUTOFINISH_STATE_DIR:-/var/lib/ai-ops-runner/soma_auto_finish}"
MAX_FAIL_PER_DAY="${OPENCLAW_SOMA_AUTOFINISH_MAX_FAIL_PER_DAY:-3}"

mkdir -p "$STATE_DIR"

# --- Check enabled ---
if [ ! -f "$CONFIG_FLAG" ]; then
  echo "soma_auto_finish_daily: DISABLED (no $CONFIG_FLAG). Exiting."
  exit 0
fi

# --- Fail count (per-day) ---
TODAY="$(date -u +%Y-%m-%d)"
FAIL_FILE="$STATE_DIR/fail_count_$TODAY.txt"
FAIL_COUNT="$(cat "$FAIL_FILE" 2>/dev/null || echo 0)"

if [ "$FAIL_COUNT" -ge "$MAX_FAIL_PER_DAY" ]; then
  echo "soma_auto_finish_daily: SKIP — already failed $FAIL_COUNT times today (max $MAX_FAIL_PER_DAY)."
  exit 0
fi

# --- Run auto_finish ---
cd "$ROOT_DIR"
VENV_PY="${ROOT_DIR}/.venv-hostd/bin/python"
if [ ! -x "$VENV_PY" ]; then
  VENV_PY="python3"
fi

RC=0
OUT="$("$VENV_PY" ./ops/scripts/soma_kajabi_auto_finish.py 2>&1)" || RC=$?

if [ "$RC" -eq 0 ]; then
  echo "soma_auto_finish_daily: PASS"
  # Reset today's fail count on success
  echo "0" > "$FAIL_FILE"
  exit 0
fi

# --- Failure: increment fail count for EXIT_NODE_OFFLINE / EXIT_NODE_ENABLE_FAILED ---
if echo "$OUT" | grep -qE "EXIT_NODE_OFFLINE|EXIT_NODE_ENABLE_FAILED"; then
  FAIL_COUNT=$(( FAIL_COUNT + 1 ))
  echo "$FAIL_COUNT" > "$FAIL_FILE"
  echo "soma_auto_finish_daily: FAIL (exit node offline). fail_count=$FAIL_COUNT/$MAX_FAIL_PER_DAY today."
fi

exit "$RC"

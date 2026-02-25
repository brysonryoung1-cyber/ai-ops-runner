#!/usr/bin/env bash
# kajabi_ui_ensure.sh — Ensure Kajabi Chromium window is visible on noVNC DISPLAY.
#
# Called before WAITING_FOR_HUMAN to eliminate "black noVNC screen".
# Uses DISPLAY from /etc/ai-ops-runner/config/novnc_display.env (fallback :99).
# If Chromium running: bring to front (wmctrl/xdotool). If not: launch with persistent profile.
# Exit 0 = Chromium launched or brought to front. Exit 1 = failed (caller may retry/restart).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
KAJABI_PROFILE="/var/lib/openclaw/kajabi_chrome_profile"
KAJABI_PRODUCTS_URL="https://app.kajabi.com/admin/products"

# Load canonical config
DISPLAY_NUM=":99"
if [ -f /etc/ai-ops-runner/config/novnc_display.env ]; then
  set -a
  # shellcheck source=/dev/null
  source /etc/ai-ops-runner/config/novnc_display.env
  set +a
  DISPLAY_NUM="${DISPLAY:-:99}"
fi
export DISPLAY="$DISPLAY_NUM"

_chromium_running() {
  pgrep -f "chromium.*kajabi_chrome_profile|chromium.*$KAJABI_PROFILE" 2>/dev/null | head -1
}

_bring_to_front() {
  if command -v wmctrl >/dev/null 2>&1; then
    wmctrl -a "Chromium" 2>/dev/null && return 0
    wmctrl -a "Kajabi" 2>/dev/null && return 0
    wmctrl -l 2>/dev/null | grep -iE "chromium|kajabi" | head -1 | while read -r id _; do
      wmctrl -i -a "$id" 2>/dev/null && return 0
    done
  fi
  if command -v xdotool >/dev/null 2>&1; then
    xdotool search --name "Chromium" windowactivate 2>/dev/null && return 0
    xdotool search --name "Kajabi" windowactivate 2>/dev/null && return 0
  fi
  return 1
}

_launch_chromium() {
  mkdir -p "$KAJABI_PROFILE"
  chmod 700 "$KAJABI_PROFILE" 2>/dev/null || true
  if command -v chromium >/dev/null 2>&1; then
    chromium --user-data-dir="$KAJABI_PROFILE" --no-sandbox --disable-dev-shm-usage \
      --window-size=1280,720 "$KAJABI_PRODUCTS_URL" 2>/dev/null &
  elif command -v chromium-browser >/dev/null 2>&1; then
    chromium-browser --user-data-dir="$KAJABI_PROFILE" --no-sandbox --disable-dev-shm-usage \
      --window-size=1280,720 "$KAJABI_PRODUCTS_URL" 2>/dev/null &
  else
    echo "kajabi_ui_ensure: chromium not found" >&2
    return 1
  fi
  sleep 3
  _chromium_running >/dev/null
}

if _chromium_running >/dev/null; then
  if _bring_to_front; then
    echo "kajabi_ui_ensure: Chromium brought to front" >&2
    exit 0
  fi
  echo "kajabi_ui_ensure: Chromium running, focus tools unavailable" >&2
  exit 0
fi

if _launch_chromium; then
  echo "kajabi_ui_ensure: Chromium launched" >&2
  exit 0
fi
echo "kajabi_ui_ensure: failed to launch Chromium" >&2
exit 1

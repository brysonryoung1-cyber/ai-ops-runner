#!/usr/bin/env bash
# novnc_supervisor.sh — Supervised noVNC stack: Xvfb + x11vnc + websockify.
#
# Invariant: Start Xvfb → wait for X11 socket → start x11vnc → start websockify.
# If any dies, restart the stack. Writes status to /run/openclaw/novnc_status.json.
# Run via systemd (openclaw-novnc.service). Does NOT exit just because there is no browser tab.
#
# Env: OPENCLAW_NOVNC_RUN_ID, OPENCLAW_NOVNC_ARTIFACT_DIR, OPENCLAW_NOVNC_PORT, OPENCLAW_NOVNC_DISPLAY, OPENCLAW_NOVNC_VNC_PORT
set -euo pipefail

RUN_ID="${OPENCLAW_NOVNC_RUN_ID:-novnc}"
ARTIFACT_DIR="${OPENCLAW_NOVNC_ARTIFACT_DIR:-/run/openclaw-novnc}"
NOVNC_PORT="${OPENCLAW_NOVNC_PORT:-6080}"
DISPLAY_NUM="${OPENCLAW_NOVNC_DISPLAY:-:99}"
VNC_PORT="${OPENCLAW_NOVNC_VNC_PORT:-5900}"

# Canonical status path per spec
STATUS_DIR="/run/openclaw"
STATUS_FILE="$STATUS_DIR/novnc_status.json"

# Ensure dirs exist (service creates these)
mkdir -p "$ARTIFACT_DIR"
mkdir -p "$STATUS_DIR"
mkdir -p /var/lib/openclaw/kajabi_chrome_profile 2>/dev/null || true

write_status() {
  local ok="$1"
  local last_error="${2:-}"
  local xvfb_pid="${3:-}"
  local x11vnc_pid="${4:-}"
  local websockify_pid="${5:-}"
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  export _WS_OK="$ok" _WS_ERR="$last_error" _WS_XVFB="$xvfb_pid" _WS_X11="$x11vnc_pid" _WS_WS="$websockify_pid"
  export _WS_TS="$ts" _WS_FILE="$STATUS_FILE" _WS_DISPLAY="$DISPLAY_NUM" _WS_VNC="$VNC_PORT" _WS_WS_PORT="$NOVNC_PORT" _WS_RUN="$RUN_ID"
  python3 - "$STATUS_FILE" <<'PYEOF' 2>/dev/null || true
import json, os, sys
d = {
  "ok": os.environ.get("_WS_OK") == "true",
  "display": os.environ.get("_WS_DISPLAY", ":99"),
  "vnc_port": int(os.environ.get("_WS_VNC", "5900")),
  "ws_port": int(os.environ.get("_WS_WS_PORT", "6080")),
  "started_at": os.environ.get("_WS_TS", ""),
  "last_error": os.environ.get("_WS_ERR") or None,
  "pid_xvfb": int(os.environ.get("_WS_XVFB")) if os.environ.get("_WS_XVFB") else None,
  "pid_x11vnc": int(os.environ.get("_WS_X11")) if os.environ.get("_WS_X11") else None,
  "pid_websockify": int(os.environ.get("_WS_WS")) if os.environ.get("_WS_WS") else None,
  "run_id": os.environ.get("_WS_RUN", "novnc"),
}
with open(sys.argv[1], "w") as f:
    json.dump(d, f, indent=2)
PYEOF
}

cleanup() {
  write_status "false" "shutdown" "$XVFB_PID" "$X11VNC_PID" "$WEBSOCKIFY_PID"
  for pid in $WEBSOCKIFY_PID $X11VNC_PID; do
    [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
  done
  [ -n "$XVFB_PID" ] && kill -TERM "$XVFB_PID" 2>/dev/null || true
  exit 0
}
trap cleanup TERM INT

# ── 1) Start Xvfb :99 ──
# Remove stale X lock only for this DISPLAY (avoid touching other displays)
XVFB_PID=""
LOCK_FILE="/tmp/.X${DISPLAY_NUM#:}-lock"
X11_SOCKET="/tmp/.X11-unix/X${DISPLAY_NUM#:}"

if [ -f "$LOCK_FILE" ]; then
  OLD_PID="$(cat "$LOCK_FILE" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Xvfb already running on $DISPLAY_NUM (pid $OLD_PID)" >&2
    XVFB_PID="$OLD_PID"
  else
    echo "Removing stale X lock for $DISPLAY_NUM (pid $OLD_PID not running)" >&2
    rm -f "$LOCK_FILE"
  fi
fi

if [ -z "$XVFB_PID" ]; then
  Xvfb "$DISPLAY_NUM" -screen 0 1280x720x24 -ac -nolisten tcp &
  XVFB_PID=$!
  echo "Xvfb starting on $DISPLAY_NUM (pid $XVFB_PID)" >&2
fi

# ── 2) Wait for /tmp/.X11-unix/X99 to exist ──
for i in $(seq 1 30); do
  if [ -S "$X11_SOCKET" ]; then
    echo "X11 socket ready: $X11_SOCKET" >&2
    break
  fi
  if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    echo "Xvfb failed to start" >&2
    write_status "false" "Xvfb exited before socket" "$XVFB_PID" "" ""
    exit 1
  fi
  sleep 1
done
if [ ! -S "$X11_SOCKET" ]; then
  echo "X11 socket $X11_SOCKET did not appear" >&2
  write_status "false" "X11 socket timeout" "$XVFB_PID" "" ""
  exit 1
fi

export DISPLAY="$DISPLAY_NUM"

# ── 3) Build websockify cmd ──
BIND_ADDR="0.0.0.0"
WEB_DIR="/usr/share/novnc"
[ -f "$WEB_DIR/vnc.html" ] || WEB_DIR=""

WS_BASE=()
if command -v websockify >/dev/null 2>&1; then
  WS_BASE=(websockify)
else
  WS_BASE=(python3 -m websockify)
fi
WS_ARGS=("${WS_BASE[@]}")
# Harden: heartbeat keeps WS alive; idle-timeout=0 prevents server exit when idle
if "${WS_BASE[@]}" --help 2>&1 | grep -qE "\-\-heartbeat"; then
  WS_ARGS+=(--heartbeat 30)
fi
if "${WS_BASE[@]}" --help 2>&1 | grep -qE "\-\-idle-timeout"; then
  WS_ARGS+=(--idle-timeout 0)
fi
[ -n "$WEB_DIR" ] && WS_ARGS+=(--web "$WEB_DIR") || true
WS_ARGS+=("$BIND_ADDR:$NOVNC_PORT" "127.0.0.1:$VNC_PORT")

# ── 4) Loop: x11vnc + websockify with auto-restart ──
while true; do
  # Start x11vnc on localhost:<VNC_PORT> with -forever -shared and explicit -display
  x11vnc -display "$DISPLAY_NUM" -rfbport "$VNC_PORT" -localhost -nopw -forever -shared -noxdamage -repeat -threads &
  X11VNC_PID=$!
  sleep 1
  if ! kill -0 "$X11VNC_PID" 2>/dev/null; then
    echo "x11vnc failed to start" >&2
    write_status "false" "x11vnc failed" "$XVFB_PID" "" ""
    exit 1
  fi

  # Start websockify bound to 0.0.0.0:6080 proxying to 127.0.0.1:<VNC_PORT>
  "${WS_ARGS[@]}" &
  WEBSOCKIFY_PID=$!
  sleep 1
  if ! kill -0 "$WEBSOCKIFY_PID" 2>/dev/null; then
    kill -TERM "$X11VNC_PID" 2>/dev/null || true
    echo "websockify failed to start" >&2
    write_status "false" "websockify failed" "$XVFB_PID" "$X11VNC_PID" ""
    exit 1
  fi

  write_status "true" "" "$XVFB_PID" "$X11VNC_PID" "$WEBSOCKIFY_PID"
  echo "noVNC stack up: x11vnc=$X11VNC_PID websockify=$WEBSOCKIFY_PID vnc=$VNC_PORT ws=$NOVNC_PORT" >&2

  # Wait for either to exit; then restart both
  while kill -0 "$X11VNC_PID" 2>/dev/null && kill -0 "$WEBSOCKIFY_PID" 2>/dev/null; do
    sleep 5
  done

  kill -TERM "$X11VNC_PID" 2>/dev/null || true
  kill -TERM "$WEBSOCKIFY_PID" 2>/dev/null || true
  wait "$X11VNC_PID" 2>/dev/null || true
  wait "$WEBSOCKIFY_PID" 2>/dev/null || true
  echo "noVNC subprocess exited; restarting in 2s" >&2
  write_status "false" "subprocess exited, restarting" "$XVFB_PID" "" ""
  sleep 2
done

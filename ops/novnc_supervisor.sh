#!/usr/bin/env bash
# novnc_supervisor.sh — Supervised noVNC stack: Xvfb + x11vnc + websockify.
#
# Idempotent, logs to journald. If x11vnc or websockify exits, restarts them.
# Run via systemd (openclaw-novnc.service). Writes status to artifact_dir.
#
# Args: run_id artifact_dir port display
# Env override: OPENCLAW_NOVNC_RUN_ID, OPENCLAW_NOVNC_ARTIFACT_DIR, OPENCLAW_NOVNC_PORT, OPENCLAW_NOVNC_DISPLAY
set -euo pipefail

RUN_ID="${OPENCLAW_NOVNC_RUN_ID:-${1:-novnc}}"
ARTIFACT_DIR="${OPENCLAW_NOVNC_ARTIFACT_DIR:-${2:-/run/openclaw-novnc}}"
NOVNC_PORT="${OPENCLAW_NOVNC_PORT:-${3:-6080}}"
DISPLAY_NUM="${OPENCLAW_NOVNC_DISPLAY:-${4:-:99}}"

# Ensure artifact dir exists
mkdir -p "$ARTIFACT_DIR"

write_status() {
  local running="$1"
  local xvfb_pid="${2:-}"
  local x11vnc_pid="${3:-}"
  local websockify_pid="${4:-}"
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  cat >"$ARTIFACT_DIR/novnc_status.json" <<EOF
{"running":$running,"ports":{"vnc":5900,"websockify":$NOVNC_PORT},"pids":{"xvfb":$xvfb_pid,"x11vnc":$x11vnc_pid,"websockify":$websockify_pid},"timestamps":{"updated":"$ts"},"run_id":"$RUN_ID"}
EOF
}

cleanup() {
  write_status "false" "" "" ""
  for pid in $WEBSOCKIFY_PID $X11VNC_PID; do
    [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
  done
  [ -n "$XVFB_PID" ] && kill -TERM "$XVFB_PID" 2>/dev/null || true
  exit 0
}
trap cleanup TERM INT

# Lock file for Xvfb display (idempotent: reuse if valid)
XVFB_PID=""
LOCK_FILE="/tmp/.X${DISPLAY_NUM#:}-lock"
if [ -f "$LOCK_FILE" ]; then
  OLD_PID="$(cat "$LOCK_FILE" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Xvfb already running on $DISPLAY_NUM (pid $OLD_PID)" >&2
    XVFB_PID="$OLD_PID"
  else
    rm -f "$LOCK_FILE"
  fi
fi
if [ -z "$XVFB_PID" ]; then
  Xvfb "$DISPLAY_NUM" -screen 0 1280x720x24 -ac -nolisten tcp &
  XVFB_PID=$!
  sleep 2
  if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    echo "Xvfb failed to start" >&2
    write_status "false" "" "" ""
    exit 1
  fi
  echo "Xvfb started on $DISPLAY_NUM (pid $XVFB_PID)" >&2
fi

export DISPLAY="$DISPLAY_NUM"

# Bind websockify to 0.0.0.0 (UFW restricts to Tailscale)
BIND_ADDR="0.0.0.0"
WEB_DIR="/usr/share/novnc"
[ -f "$WEB_DIR/vnc.html" ] || WEB_DIR=""

# Build websockify cmd (--heartbeat 30 for keepalive if supported by novnc/websockify)
WS_BASE=()
if command -v websockify >/dev/null 2>&1; then
  WS_BASE=(websockify)
else
  WS_BASE=(python3 -m websockify)
fi
WS_ARGS=("${WS_BASE[@]}")
if "${WS_BASE[@]}" --help 2>&1 | grep -qE "\-\-heartbeat"; then
  WS_ARGS+=(--heartbeat 30)
fi
[ -n "$WEB_DIR" ] && WS_ARGS+=(--web "$WEB_DIR") || true
WS_ARGS+=("$BIND_ADDR:$NOVNC_PORT" "127.0.0.1:5900")

# Loop: x11vnc and websockify with auto-restart
while true; do
  # Start x11vnc
  x11vnc -display "$DISPLAY_NUM" -rfbport 5900 -localhost -nopw -forever -shared -noxdamage -repeat -threads &
  X11VNC_PID=$!
  sleep 1
  if ! kill -0 "$X11VNC_PID" 2>/dev/null; then
    echo "x11vnc failed to start" >&2
    write_status "false" "$XVFB_PID" "" ""
    exit 1
  fi

  # Start websockify
  "${WS_ARGS[@]}" &
  WEBSOCKIFY_PID=$!
  sleep 1
  if ! kill -0 "$WEBSOCKIFY_PID" 2>/dev/null; then
    kill -TERM "$X11VNC_PID" 2>/dev/null || true
    echo "websockify failed to start" >&2
    write_status "false" "$XVFB_PID" "$X11VNC_PID" ""
    exit 1
  fi

  write_status "true" "$XVFB_PID" "$X11VNC_PID" "$WEBSOCKIFY_PID"
  echo "noVNC stack up: x11vnc=$X11VNC_PID websockify=$WEBSOCKIFY_PID port=$NOVNC_PORT" >&2

  # Wait for either to exit; then restart both
  while kill -0 "$X11VNC_PID" 2>/dev/null && kill -0 "$WEBSOCKIFY_PID" 2>/dev/null; do
    sleep 5
  done

  kill -TERM "$X11VNC_PID" 2>/dev/null || true
  kill -TERM "$WEBSOCKIFY_PID" 2>/dev/null || true
  wait "$X11VNC_PID" 2>/dev/null || true
  wait "$WEBSOCKIFY_PID" 2>/dev/null || true
  echo "noVNC subprocess exited; restarting in 2s" >&2
  sleep 2
done

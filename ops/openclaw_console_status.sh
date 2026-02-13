#!/usr/bin/env bash
# ops/openclaw_console_status.sh — Print OpenClaw Console status
#
# Shows: URL, PID, and last 30 lines of server log.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_BASE="$REPO_ROOT/logs/openclaw_console"
PID_FILE="$LOG_BASE/console.pid"
PORT="${OPENCLAW_CONSOLE_PORT:-8787}"
LATEST_LOG="$LOG_BASE/latest/server.log"

echo "═══ OpenClaw Console Status ═══"
echo ""

# ─── PID check ────────────────────────────────────────────────────────────────

running=false
if [ -f "$PID_FILE" ]; then
  PID=$(cat "$PID_FILE" 2>/dev/null || true)
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    echo "Status:  RUNNING"
    echo "PID:     $PID"
    echo "Port:    $PORT"
    echo "URL:     http://127.0.0.1:$PORT"
    running=true
  else
    echo "Status:  STOPPED (stale PID file)"
    rm -f "$PID_FILE"
  fi
else
  # Fallback: check port
  PIDS=$(lsof -ti :"$PORT" 2>/dev/null || true)
  if [ -n "$PIDS" ]; then
    echo "Status:  RUNNING (no PID file, found on port $PORT)"
    echo "PID(s):  $PIDS"
    echo "Port:    $PORT"
    echo "URL:     http://127.0.0.1:$PORT"
    running=true
  else
    echo "Status:  STOPPED"
  fi
fi

# ─── Token status ─────────────────────────────────────────────────────────────

echo ""
if command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/openclaw_console_token.py" ]; then
  python3 "$SCRIPT_DIR/openclaw_console_token.py" status 2>/dev/null || echo "Token:   unknown"
else
  echo "Token:   (token manager not found)"
fi

# ─── Log tail ─────────────────────────────────────────────────────────────────

echo ""
if [ -f "$LATEST_LOG" ]; then
  echo "── Last 30 lines of server log ──"
  tail -30 "$LATEST_LOG"
else
  echo "No server log found."
fi

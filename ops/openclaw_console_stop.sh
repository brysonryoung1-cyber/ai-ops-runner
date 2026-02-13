#!/usr/bin/env bash
# ops/openclaw_console_stop.sh — Stop the OpenClaw Console
#
# Clean shutdown by PID file, fallback to port scan.
# Exits 0 even if already stopped (idempotent).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_BASE="$REPO_ROOT/logs/openclaw_console"
PID_FILE="$LOG_BASE/console.pid"
PORT="${OPENCLAW_CONSOLE_PORT:-8787}"

stopped=false

# ─── Method 1: PID file ──────────────────────────────────────────────────────

if [ -f "$PID_FILE" ]; then
  PID=$(cat "$PID_FILE" 2>/dev/null || true)
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    echo "Stopping OpenClaw Console (pid=$PID)..."
    kill "$PID" 2>/dev/null || true
    # Wait up to 5 seconds for graceful shutdown
    for _ in $(seq 1 10); do
      if ! kill -0 "$PID" 2>/dev/null; then break; fi
      sleep 0.5
    done
    # Force kill if still running
    if kill -0 "$PID" 2>/dev/null; then
      echo "Force-killing pid=$PID..."
      kill -9 "$PID" 2>/dev/null || true
    fi
    stopped=true
  fi
  rm -f "$PID_FILE"
fi

# ─── Method 2: fallback — find by port ───────────────────────────────────────

if ! $stopped; then
  PIDS=$(lsof -ti :"$PORT" 2>/dev/null || true)
  if [ -n "$PIDS" ]; then
    echo "Stopping process(es) on port $PORT..."
    echo "$PIDS" | xargs kill 2>/dev/null || true
    sleep 1
    # Force kill stragglers
    PIDS=$(lsof -ti :"$PORT" 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
      echo "$PIDS" | xargs kill -9 2>/dev/null || true
    fi
    stopped=true
  fi
fi

# ─── Report ──────────────────────────────────────────────────────────────────

if $stopped; then
  echo "OpenClaw Console stopped."
else
  echo "OpenClaw Console is not running."
fi
exit 0

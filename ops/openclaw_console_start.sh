#!/usr/bin/env bash
# ops/openclaw_console_start.sh — Start OpenClaw Console in production mode
#
# Binds to 127.0.0.1 ONLY (never 0.0.0.0).
# Port: 8787 (override with OPENCLAW_CONSOLE_PORT).
# Writes pid + logs under logs/openclaw_console/<timestamp>/.
# Idempotent: if already running, prints status and exits 0.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONSOLE_DIR="$REPO_ROOT/apps/openclaw-console"
PORT="${OPENCLAW_CONSOLE_PORT:-8787}"
LOG_BASE="$REPO_ROOT/logs/openclaw_console"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$LOG_BASE/$TIMESTAMP"
PID_FILE="$LOG_BASE/console.pid"

# ─── Idempotency: check if already running ───────────────────────────────────

if [ -f "$PID_FILE" ]; then
  OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "OpenClaw Console is already running."
    echo "  PID:  $OLD_PID"
    echo "  Port: $PORT"
    echo "  URL:  http://127.0.0.1:$PORT"
    exit 0
  fi
  # Stale PID file — remove it
  rm -f "$PID_FILE"
fi

# ─── Preflight ────────────────────────────────────────────────────────────────

if [ ! -d "$CONSOLE_DIR/.next" ]; then
  echo "ERROR: Production build not found. Run first:" >&2
  echo "  ./ops/openclaw_console_build.sh" >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: Node.js is not installed." >&2
  exit 1
fi

# ─── Create log directory ────────────────────────────────────────────────────

mkdir -p "$LOG_DIR"

# ─── Load auth token from Keychain (if configured) ──────────────────────────

OPENCLAW_CONSOLE_TOKEN=""
if command -v python3 >/dev/null 2>&1 && [ -f "$SCRIPT_DIR/openclaw_console_token.py" ]; then
  OPENCLAW_CONSOLE_TOKEN=$(python3 "$SCRIPT_DIR/openclaw_console_token.py" _get 2>/dev/null || true)
fi
export OPENCLAW_CONSOLE_TOKEN

# ─── Start production server ─────────────────────────────────────────────────
# Bound to 127.0.0.1 — NEVER 0.0.0.0.

cd "$CONSOLE_DIR"
nohup npx next start --hostname 127.0.0.1 --port "$PORT" \
  > "$LOG_DIR/server.log" 2>&1 &
SERVER_PID=$!

echo "$SERVER_PID" > "$PID_FILE"

# Symlink latest log directory for easy access
ln -sfn "$LOG_DIR" "$LOG_BASE/latest"

# Wait briefly and verify the process started
sleep 2
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "ERROR: Server process died immediately. Check logs:" >&2
  echo "  $LOG_DIR/server.log" >&2
  tail -20 "$LOG_DIR/server.log" 2>/dev/null >&2 || true
  rm -f "$PID_FILE"
  exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  OpenClaw Console — PRODUCTION                      ║"
echo "║  http://127.0.0.1:${PORT}                              ║"
echo "║                                                     ║"
echo "║  Private — bound to 127.0.0.1 only                 ║"
echo "║  PID: $(printf '%-6s' "$SERVER_PID")                                        ║"
echo "║  Logs: logs/openclaw_console/latest/server.log      ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
if [ -n "$OPENCLAW_CONSOLE_TOKEN" ]; then
  echo "Auth token: loaded from Keychain"
else
  echo "Auth token: not configured (API unprotected)"
  echo "  → Run: python3 ops/openclaw_console_token.py rotate"
fi

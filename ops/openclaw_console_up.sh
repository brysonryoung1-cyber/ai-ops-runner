#!/usr/bin/env bash
# ops/openclaw_console_up.sh — Start the OpenClaw Console (private, localhost only)
#
# Usage:
#   ./ops/openclaw_console_up.sh
#
# The console binds to 127.0.0.1:8787 and is NOT accessible from the network.
# Requires Tailscale to be running for SSH connectivity to aiops-1.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONSOLE_DIR="$REPO_ROOT/apps/openclaw-console"
PORT=8787

# ─── Preflight checks ──────────────────────────────────────────────────────────

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: Node.js is not installed. Install it from https://nodejs.org" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm is not installed." >&2
  exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
  echo "ERROR: ssh is not installed." >&2
  exit 1
fi

if [ ! -d "$CONSOLE_DIR" ]; then
  echo "ERROR: Console directory not found at $CONSOLE_DIR" >&2
  exit 1
fi

# ─── .env.local setup ──────────────────────────────────────────────────────────

if [ ! -f "$CONSOLE_DIR/.env.local" ]; then
  echo "Creating .env.local from .env.example..."
  cp "$CONSOLE_DIR/.env.example" "$CONSOLE_DIR/.env.local"
  echo "  → Edit $CONSOLE_DIR/.env.local if the AIOPS_HOST differs."
fi

# ─── Install dependencies (once) ───────────────────────────────────────────────

if [ ! -d "$CONSOLE_DIR/node_modules" ]; then
  echo "Installing dependencies (first run)..."
  (cd "$CONSOLE_DIR" && npm ci --silent)
fi

# ─── Start ──────────────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  OpenClaw Console                                   ║"
echo "║  http://127.0.0.1:${PORT}                              ║"
echo "║                                                     ║"
echo "║  Private — bound to localhost only                  ║"
echo "║  Ctrl+C to stop                                     ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

cd "$CONSOLE_DIR"
exec npm run dev

#!/usr/bin/env bash
# ops/openclaw_console_build.sh — Build the OpenClaw Console for production
#
# Usage:
#   ./ops/openclaw_console_build.sh
#
# Installs dependencies if needed, runs Next.js production build.
# Exits 0 with "OK: build complete" on success.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONSOLE_DIR="$REPO_ROOT/apps/openclaw-console"

# ─── Preflight ────────────────────────────────────────────────────────────────

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: Node.js is not installed. Install from https://nodejs.org" >&2
  exit 1
fi

NODE_MAJOR=$(node -e 'console.log(process.versions.node.split(".")[0])')
if [ "$NODE_MAJOR" -lt 18 ]; then
  echo "ERROR: Node.js >= 18 required (found v$(node -v))" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm is not installed." >&2
  exit 1
fi

if [ ! -d "$CONSOLE_DIR" ]; then
  echo "ERROR: Console directory not found at $CONSOLE_DIR" >&2
  exit 1
fi

# ─── .env.local setup ────────────────────────────────────────────────────────

if [ ! -f "$CONSOLE_DIR/.env.local" ]; then
  echo "Creating .env.local from .env.example..."
  cp "$CONSOLE_DIR/.env.example" "$CONSOLE_DIR/.env.local"
  echo "  → Edit $CONSOLE_DIR/.env.local if the AIOPS_HOST differs."
fi

# ─── Install dependencies ────────────────────────────────────────────────────

echo "Installing dependencies..."
(cd "$CONSOLE_DIR" && npm ci --silent)

# ─── Build ────────────────────────────────────────────────────────────────────

echo "Building for production..."
(cd "$CONSOLE_DIR" && npm run build)

echo ""
echo "OK: build complete"

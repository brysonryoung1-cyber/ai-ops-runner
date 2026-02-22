#!/usr/bin/env bash
# ensure_hostd_venv_playwright.sh — Idempotent venv with Playwright + Chromium for hostd.
# Creates /opt/ai-ops-runner/.venv-hostd with playwright and chromium (Ubuntu deps).
# Writes marker /var/lib/ai-ops-runner/hostd_playwright_ok.json for fast reruns.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="${REPO_ROOT:-$ROOT_DIR}/.venv-hostd"
MARKER_FILE="/var/lib/ai-ops-runner/hostd_playwright_ok.json"
PLAYWRIGHT_BROWSERS_DIR="/var/lib/ai-ops-runner/playwright-browsers"

# Idempotent fast path: marker exists, python imports playwright, chromium cache exists
if [ -f "$MARKER_FILE" ]; then
  PYTHON_BIN="$VENV_DIR/bin/python"
  if [ -x "$PYTHON_BIN" ]; then
    if PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_DIR" "$PYTHON_BIN" -c "
import json, os, sys
try:
    import playwright
    with open('$MARKER_FILE') as f:
        marker = json.load(f)
    cache_dir = marker.get('chromium_cache_dir', '')
    if cache_dir and os.path.isdir(cache_dir):
        sys.exit(0)
    sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
      echo "hostd Playwright venv OK (marker valid, playwright import OK, chromium cache present)"
      exit 0
    fi
  fi
fi

# Ensure marker dir exists
sudo mkdir -p /var/lib/ai-ops-runner

# Ubuntu: install python3.12-venv
if [ -f /etc/os-release ]; then
  . /etc/os-release
  if [ "${ID:-}" = "ubuntu" ] || [ "${ID_LIKE:-}" = "debian" ]; then
    sudo apt-get update -qq
    sudo apt-get install -y python3.12-venv
  fi
fi

# Create venv
if [ ! -d "$VENV_DIR" ]; then
  python3.12 -m venv "$VENV_DIR"
fi

# Upgrade pip, install playwright
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install playwright -q

# Install chromium + Ubuntu deps
export PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_DIR"
"$VENV_DIR/bin/python" -m playwright install --with-deps chromium

# Chromium cache dir (playwright stores under PLAYWRIGHT_BROWSERS_PATH)
CHROMIUM_CACHE="$PLAYWRIGHT_BROWSERS_DIR"
PLAYWRIGHT_VERSION="$("$VENV_DIR/bin/python" -c "import playwright; print(playwright.__version__)" 2>/dev/null || echo "unknown")"

# Write marker
sudo mkdir -p /var/lib/ai-ops-runner
sudo tee "$MARKER_FILE" >/dev/null <<EOF
{
  "playwright_version": "$PLAYWRIGHT_VERSION",
  "chromium_cache_dir": "$CHROMIUM_CACHE",
  "venv_dir": "$VENV_DIR"
}
EOF

echo "hostd Playwright venv ready: $VENV_DIR (playwright $PLAYWRIGHT_VERSION, chromium at $CHROMIUM_CACHE)"

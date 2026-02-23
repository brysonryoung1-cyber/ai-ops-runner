#!/usr/bin/env bash
# install_kajabi_interactive_deps.sh — Idempotent install of Xvfb, x11vnc, websockify
# for kajabi_capture_interactive (headed Chromium over noVNC when Cloudflare blocks).
#
# Run from repo root. No manual apt/pip; all via this script.
# Writes minimal logs; no secrets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

echo "==> install_kajabi_interactive_deps.sh (idempotent)"

# apt: Xvfb, x11vnc; Chromium runtime deps (Playwright headed mode): libnss3, fonts
apt-get update -qq
apt-get install -y --no-install-recommends \
  xvfb \
  x11vnc \
  libnss3 \
  libnspr4 \
  libatk1.0-0 \
  libatk-bridge2.0-0 \
  libcups2 \
  libdrm2 \
  libxkbcommon0 \
  libxcomposite1 \
  libxdamage1 \
  libxfixes3 \
  libxrandr2 \
  libgbm1 \
  libasound2 \
  fonts-liberation \
  fonts-noto-core \
  2>/dev/null || true

# websockify: pip (more portable than apt novnc)
if ! command -v websockify >/dev/null 2>&1 && ! python3 -c "import websockify" 2>/dev/null; then
  pip3 install --break-system-packages websockify 2>/dev/null || \
  pip3 install websockify 2>/dev/null || true
fi

echo "  Xvfb:       $(command -v Xvfb 2>/dev/null || echo 'MISSING')"
echo "  x11vnc:     $(command -v x11vnc 2>/dev/null || echo 'MISSING')"
echo "  websockify: $(command -v websockify 2>/dev/null || echo 'python3 -m websockify')"

if ! command -v Xvfb >/dev/null 2>&1; then
  echo "ERROR: Xvfb not installed" >&2
  exit 1
fi
if ! command -v x11vnc >/dev/null 2>&1; then
  echo "ERROR: x11vnc not installed" >&2
  exit 1
fi
if ! command -v websockify >/dev/null 2>&1 && ! python3 -c "import websockify" 2>/dev/null; then
  echo "ERROR: websockify not installed" >&2
  exit 1
fi

echo "  install_kajabi_interactive_deps: PASS"

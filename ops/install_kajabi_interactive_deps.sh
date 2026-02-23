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
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  xvfb \
  x11vnc \
  python3-websockify \
  novnc \
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
  fonts-liberation \
  fonts-noto-core

# libasound2: Ubuntu 24.04 uses libasound2t64
apt-get install -y --no-install-recommends libasound2t64 2>/dev/null || \
  apt-get install -y --no-install-recommends libasound2 2>/dev/null || true

# websockify: prefer apt python3-websockify; pip fallback is opt-in and must be pinned
if ! command -v websockify >/dev/null 2>&1 && ! python3 -c "import websockify" 2>/dev/null; then
  PIP_WEBSOCKIFY_SPEC="${PIP_WEBSOCKIFY_SPEC:-}"
  if [ -n "$PIP_WEBSOCKIFY_SPEC" ]; then
    case "$PIP_WEBSOCKIFY_SPEC" in
      websockify==*)
        ;;
      *)
        echo "ERROR: PIP_WEBSOCKIFY_SPEC must be pinned like websockify==0.12.0" >&2
        exit 1
        ;;
    esac
    if pip3 install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
      pip3 install --break-system-packages "$PIP_WEBSOCKIFY_SPEC" 2>/dev/null || true
    else
      pip3 install "$PIP_WEBSOCKIFY_SPEC" 2>/dev/null || true
    fi
  else
    echo "  websockify apt package unavailable; skipping unpinned pip fallback (set PIP_WEBSOCKIFY_SPEC=websockify==<version> to enable)"
  fi
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

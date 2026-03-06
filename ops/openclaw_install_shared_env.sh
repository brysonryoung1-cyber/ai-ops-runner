#!/usr/bin/env bash
# openclaw_install_shared_env.sh — Install the shared OpenClaw systemd environment file.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_SRC="$SCRIPT_DIR/systemd/openclaw.env"
ENV_DIR="/etc/ai-ops-runner"
ENV_DST="$ENV_DIR/openclaw.env"

echo "=== openclaw_install_shared_env.sh ==="
sudo install -d -m 0755 "$ENV_DIR"
sudo install -m 0644 "$ENV_SRC" "$ENV_DST"
echo "  Installed shared environment file: $ENV_DST"

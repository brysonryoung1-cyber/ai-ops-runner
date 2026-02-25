#!/usr/bin/env bash
# install_local_secrets.sh — One-time helper to install VERDICT_HMAC_KEY as fallback file.
#
# Usage: cat your_verdict_hmac_key | ./ops/install_local_secrets.sh
#   OR:  ./ops/install_local_secrets.sh < path/to/key
#
# Creates ~/.config/ai-ops-runner/ with chmod 700.
# Writes verdict_hmac_key from stdin with chmod 600.
# Prints nothing sensitive.
set -euo pipefail

CONFIG_DIR="${HOME:-/tmp}/.config/ai-ops-runner"
KEY_FILE="$CONFIG_DIR/verdict_hmac_key"

mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"

if [ ! -t 0 ]; then
  cat > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  echo "Installed verdict_hmac_key (fallback) at $KEY_FILE"
else
  echo "Usage: cat your_key | $0  OR  $0 < path/to/key" >&2
  exit 1
fi

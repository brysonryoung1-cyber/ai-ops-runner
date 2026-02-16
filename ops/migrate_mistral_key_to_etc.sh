#!/usr/bin/env bash
# migrate_mistral_key_to_etc.sh — One-time VPS: copy Mistral key from /opt to /etc for container mount.
# Containers mount /etc/ai-ops-runner/secrets -> /run/openclaw_secrets. Key must exist at /etc.
# Idempotent: if /etc already has the key, no-op. If /opt has it, copy and set perms (1000:1000, 0640).
set -euo pipefail

SRC="/opt/ai-ops-runner/secrets/mistral_api_key"
DEST="/etc/ai-ops-runner/secrets/mistral_api_key"
DEST_DIR="/etc/ai-ops-runner/secrets"

if [ -f "$DEST" ]; then
  echo "OK: $DEST already exists. No migration needed."
  exit 0
fi
if [ ! -f "$SRC" ]; then
  echo "SKIP: $SRC not found. Nothing to migrate."
  exit 0
fi

echo "Migrating Mistral key from $SRC to $DEST..."
sudo mkdir -p "$DEST_DIR"
sudo cp "$SRC" "$DEST"
sudo chown 1000:1000 "$DEST"
sudo chmod 0640 "$DEST"
echo "OK: Key at $DEST (owner 1000:1000, mode 0640). Restart stack: docker compose up -d"

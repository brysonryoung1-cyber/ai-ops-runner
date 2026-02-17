#!/bin/sh
# Load API keys from host secrets into env without printing. Then run LiteLLM proxy.
set -e
SECRETS_DIR="${OPENCLAW_SECRETS_DIR:-/run/openclaw_secrets}"
if [ -d "$SECRETS_DIR" ]; then
  [ -r "$SECRETS_DIR/openai_api_key" ] && export OPENAI_API_KEY="$(cat "$SECRETS_DIR/openai_api_key")"
  [ -r "$SECRETS_DIR/mistral_api_key" ] && export MISTRAL_API_KEY="$(cat "$SECRETS_DIR/mistral_api_key")"
  true
fi
# Private-only: bind 127.0.0.1 (network_mode: host; Redis via 127.0.0.1:6379 on host).
exec litellm --config /etc/litellm/config.yaml --port 4000 --host 127.0.0.1

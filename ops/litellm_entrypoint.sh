#!/bin/sh
# Load API keys from host secrets into env without printing. Then run LiteLLM proxy.
set -e
SECRETS_DIR="${OPENCLAW_SECRETS_DIR:-/run/openclaw_secrets}"
if [ -d "$SECRETS_DIR" ]; then
  [ -r "$SECRETS_DIR/openai_api_key" ] && export OPENAI_API_KEY="$(cat "$SECRETS_DIR/openai_api_key")"
  [ -r "$SECRETS_DIR/mistral_api_key" ] && export MISTRAL_API_KEY="$(cat "$SECRETS_DIR/mistral_api_key")"
  # Optional: one file with key per line for other providers
  true
fi
exec litellm --config /etc/litellm/config.yaml --port 4000 --host 0.0.0.0

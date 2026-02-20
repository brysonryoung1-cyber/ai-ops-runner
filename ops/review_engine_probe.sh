#!/usr/bin/env bash
# review_engine_probe.sh — Detect which review engines are usable (no secrets printed)
#
# Output (stdout):
#   ENGINE=<router|openai|codex|none>   (first available in deterministic order)
#   ENGINES=<comma-separated list>      (all available, deterministic order for failover)
#   SUMMARY=<short human summary>       (presence booleans only; no key values)
#
# Deterministic order: router -> openai -> codex
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

ROUTER_OK=0
OPENAI_OK=0
CODEX_OK=0

# (1) LiteLLM/router: REVIEW_BASE_URL + REVIEW_API_KEY present
if [ -n "${REVIEW_BASE_URL:-}" ] && [ -n "${REVIEW_API_KEY:-}" ]; then
  ROUTER_OK=1
fi

# (2) Direct OpenAI: OPENAI_API_KEY present (env only; no keyring resolution)
if [ -n "${OPENAI_API_KEY:-}" ]; then
  OPENAI_OK=1
fi

# (3) Codex CLI: command exists and --version succeeds
if command -v codex >/dev/null 2>&1; then
  if codex --version >/dev/null 2>&1; then
    CODEX_OK=1
  fi
fi

# Build ordered list and first engine
ENGINES_LIST=""
FIRST_ENGINE="none"

[ "$ROUTER_OK" -eq 1 ] && { [ -n "$ENGINES_LIST" ] && ENGINES_LIST="$ENGINES_LIST,router" || ENGINES_LIST="router"; [ "$FIRST_ENGINE" = "none" ] && FIRST_ENGINE="router"; }
[ "$OPENAI_OK" -eq 1 ] && { [ -n "$ENGINES_LIST" ] && ENGINES_LIST="$ENGINES_LIST,openai" || ENGINES_LIST="openai"; [ "$FIRST_ENGINE" = "none" ] && FIRST_ENGINE="openai"; }
[ "$CODEX_OK" -eq 1 ]  && { [ -n "$ENGINES_LIST" ] && ENGINES_LIST="$ENGINES_LIST,codex" || ENGINES_LIST="codex"; [ "$FIRST_ENGINE" = "none" ] && FIRST_ENGINE="codex"; }

# Summary: presence only, no secrets
SUMMARY_PARTS=""
[ "$ROUTER_OK" -eq 1 ] && SUMMARY_PARTS="${SUMMARY_PARTS:+$SUMMARY_PARTS; }REVIEW_BASE_URL+REVIEW_API_KEY=present"
[ "$OPENAI_OK" -eq 1 ] && SUMMARY_PARTS="${SUMMARY_PARTS:+$SUMMARY_PARTS; }OPENAI_API_KEY=present"
[ "$CODEX_OK" -eq 1 ]  && SUMMARY_PARTS="${SUMMARY_PARTS:+$SUMMARY_PARTS; }codex=ok"
[ -z "$SUMMARY_PARTS" ] && SUMMARY_PARTS="no engines available"

echo "ENGINE=$FIRST_ENGINE"
echo "ENGINES=${ENGINES_LIST:-none}"
echo "SUMMARY=$SUMMARY_PARTS"

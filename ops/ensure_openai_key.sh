#!/usr/bin/env bash
# ensure_openai_key.sh — Load OPENAI_API_KEY securely before Codex calls
#
# USAGE (source from another script):
#   source "$SCRIPT_DIR/ensure_openai_key.sh"
#
# After sourcing, OPENAI_API_KEY is exported into the current shell.
# If the key cannot be obtained, the script exits non-zero (fail-closed).
#
# Resolution order (handled by openai_key.py):
#   1. Already set in environment → no-op.
#   2. Python keyring (macOS Keychain backend).
#   3. Linux /etc/ai-ops-runner/secrets/openai_api_key.
#   4. macOS: interactive getpass prompt → stored in keyring for next time.
#
# SECURITY:
#   - set +x is enforced to prevent tracing/leaking in debug shells.
#   - The key is captured via $() and NEVER echoed to the terminal.
#   - On exit, OPENAI_API_KEY is scrubbed unless KEEP_OPENAI_KEY=1.

# --- Prevent tracing from leaking the key ---
set +x

# --- Determine directory of THIS script (works when sourced) ---
_ENSURE_KEY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

# --- Fast path: already set ---
if [ -n "${OPENAI_API_KEY:-}" ]; then
  export OPENAI_API_KEY
  unset _ENSURE_KEY_DIR
  return 0 2>/dev/null || exit 0
fi

# --- Call Python helper (key → stdout, messages → stderr) ---
if ! _LOADED_KEY="$(python3 "$_ENSURE_KEY_DIR/openai_key.py")"; then
  echo "FATAL: Could not obtain OPENAI_API_KEY. Pipeline stopped (fail-closed)." >&2
  unset _LOADED_KEY
  unset _ENSURE_KEY_DIR
  return 1 2>/dev/null || exit 1
fi

if [ -z "${_LOADED_KEY:-}" ]; then
  echo "FATAL: openai_key.py returned empty key. Pipeline stopped (fail-closed)." >&2
  unset _LOADED_KEY
  unset _ENSURE_KEY_DIR
  return 1 2>/dev/null || exit 1
fi

export OPENAI_API_KEY="$_LOADED_KEY"

# Scrub the temp variable immediately
unset _LOADED_KEY
unset _ENSURE_KEY_DIR

# --- Schedule key scrubbing on script exit (unless caller opts out) ---
# When KEEP_OPENAI_KEY=1, the key persists for interactive/multi-step use.
# Default: scrub after the calling script finishes.
# IMPORTANT: chain with any pre-existing EXIT trap from the caller.
if [ "${KEEP_OPENAI_KEY:-0}" != "1" ]; then
  _PREV_EXIT_TRAP="$(trap -p EXIT | sed "s/^trap -- '//;s/' EXIT$//" || true)"
  if [ -n "$_PREV_EXIT_TRAP" ]; then
    # shellcheck disable=SC2064
    trap "unset OPENAI_API_KEY 2>/dev/null; ${_PREV_EXIT_TRAP}" EXIT
  else
    trap 'unset OPENAI_API_KEY 2>/dev/null' EXIT
  fi
  unset _PREV_EXIT_TRAP
fi

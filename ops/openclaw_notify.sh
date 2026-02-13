#!/usr/bin/env bash
# openclaw_notify.sh — Outbound-only Pushover notifications for OpenClaw
#
# Usage:
#   ./ops/openclaw_notify.sh "message"
#   ./ops/openclaw_notify.sh --priority high --title "Doctor" "Check failed"
#   ./ops/openclaw_notify.sh --dry-run "Test message"
#   ./ops/openclaw_notify.sh --test
#   ./ops/openclaw_notify.sh --rate-key "check_public_ports" "FAIL: sshd public"
#
# Secrets: PUSHOVER_APP_TOKEN, PUSHOVER_USER_KEY
# Resolution: env → macOS Keychain → Linux file /etc/ai-ops-runner/secrets/
#
# NEVER prints secrets. Outbound HTTPS POST only. No inbound webhooks.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Defaults ---
PRIORITY="normal"
TITLE="OpenClaw"
DRY_RUN=0
TEST_MODE=0
RATE_KEY=""
RATE_LIMIT_SEC="${OPENCLAW_NOTIFY_RATE_LIMIT_SEC:-1800}"  # 30 min default
RATE_DIR="${OPENCLAW_NOTIFY_RATE_DIR:-/tmp/openclaw_notify_ratelimit}"
# For testing: override the curl command
OPENCLAW_NOTIFY_CURL_CMD="${OPENCLAW_NOTIFY_CURL_CMD:-curl}"

# --- Priority mapping ---
priority_value() {
  case "$1" in
    low)       echo "-1" ;;
    normal)    echo "0" ;;
    high)      echo "1" ;;
    emergency) echo "2" ;;
    *)         echo "0" ;;
  esac
}

# --- Secret loading (env → keychain → file) ---
load_secret() {
  local env_name="$1"
  local keychain_account="$2"
  local file_path="$3"

  # 1. Environment variable
  local val="${!env_name:-}"
  if [ -n "$val" ]; then
    echo "$val"
    return 0
  fi

  # 2. macOS Keychain
  if command -v security >/dev/null 2>&1; then
    val="$(security find-generic-password -a "$keychain_account" -s ai-ops-runner -w 2>/dev/null || true)"
    if [ -n "$val" ]; then
      echo "$val"
      return 0
    fi
  fi

  # 3. Linux file
  if [ -f "$file_path" ]; then
    val="$(cat "$file_path" 2>/dev/null | tr -d '[:space:]')"
    if [ -n "$val" ]; then
      echo "$val"
      return 0
    fi
  fi

  return 1
}

# --- Rate limiting ---
_sha256() {
  # Portable SHA256: try shasum first (macOS), then sha256sum (Linux)
  if command -v shasum >/dev/null 2>&1; then
    echo -n "$1" | shasum -a 256 | cut -d' ' -f1
  elif command -v sha256sum >/dev/null 2>&1; then
    echo -n "$1" | sha256sum | cut -d' ' -f1
  else
    echo "$1"  # Fallback: use raw key (still functional, just not hashed)
  fi
}

check_rate_limit() {
  local key="$1"
  if [ -z "$key" ]; then
    return 0  # No rate key → always send
  fi

  mkdir -p "$RATE_DIR"
  local hash
  hash="$(_sha256 "$key")"
  local stamp_file="$RATE_DIR/$hash"

  if [ -f "$stamp_file" ]; then
    local last_sent
    last_sent="$(cat "$stamp_file" 2>/dev/null || echo "0")"
    local now
    now="$(date +%s)"
    local elapsed=$((now - last_sent))
    if [ "$elapsed" -lt "$RATE_LIMIT_SEC" ]; then
      echo "RATE_LIMITED: $key (${elapsed}s < ${RATE_LIMIT_SEC}s)" >&2
      return 1
    fi
  fi

  # Note: timestamp is NOT written here. It is written AFTER successful send
  # to avoid suppressing alerts when delivery fails.
  return 0
}

# Write rate-limit stamp AFTER successful delivery
_mark_rate_sent() {
  local key="$1"
  if [ -z "$key" ]; then return 0; fi
  mkdir -p "$RATE_DIR"
  local hash
  hash="$(_sha256 "$key")"
  date +%s > "$RATE_DIR/$hash"
}

# --- Parse args ---
MESSAGE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --priority)  PRIORITY="$2"; shift 2 ;;
    --title)     TITLE="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=1; shift ;;
    --test)      TEST_MODE=1; shift ;;
    --rate-key)  RATE_KEY="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: openclaw_notify.sh [--priority low|normal|high|emergency] [--title T] [--dry-run] [--test] [--rate-key KEY] \"message\""
      exit 0
      ;;
    -*) echo "ERROR: Unknown flag: $1" >&2; exit 1 ;;
    *)  MESSAGE="$1"; shift ;;
  esac
done

# --- Load secrets ---
APP_TOKEN=""
USER_KEY=""

load_secrets() {
  APP_TOKEN="$(load_secret PUSHOVER_APP_TOKEN PUSHOVER_APP_TOKEN /etc/ai-ops-runner/secrets/pushover_app_token)" || {
    echo "ERROR: PUSHOVER_APP_TOKEN not found (checked: env, keychain, /etc/ai-ops-runner/secrets/)" >&2
    return 1
  }
  USER_KEY="$(load_secret PUSHOVER_USER_KEY PUSHOVER_USER_KEY /etc/ai-ops-runner/secrets/pushover_user_key)" || {
    echo "ERROR: PUSHOVER_USER_KEY not found (checked: env, keychain, /etc/ai-ops-runner/secrets/)" >&2
    return 1
  }
}

# --- Test mode ---
if [ "$TEST_MODE" -eq 1 ]; then
  echo "=== openclaw_notify.sh — connectivity test ==="
  if load_secrets; then
    echo "  PUSHOVER_APP_TOKEN: found (masked)"
    echo "  PUSHOVER_USER_KEY: found (masked)"

    if [ "$DRY_RUN" -eq 1 ]; then
      echo "  DRY_RUN: would send test message"
      echo "  PASS"
      exit 0
    fi

    # Validate with Pushover API (validate endpoint)
    # Secrets sent via stdin (heredoc) — NEVER in process argv
    VALIDATE_RC=0
    $OPENCLAW_NOTIFY_CURL_CMD -sf -X POST \
      -H "Content-Type: application/json" \
      -d @- \
      https://api.pushover.net/1/users/validate.json >/dev/null 2>&1 <<VALIDATE_JSON || VALIDATE_RC=$?
{"token":"$APP_TOKEN","user":"$USER_KEY"}
VALIDATE_JSON

    if [ "$VALIDATE_RC" -eq 0 ]; then
      echo "  Pushover API: reachable + credentials valid"
      echo "  PASS"
    else
      echo "  Pushover API: FAILED (rc=$VALIDATE_RC)" >&2
      echo "  FAIL" >&2
      exit 1
    fi
  else
    echo "  FAIL: secrets not available" >&2
    exit 1
  fi
  exit 0
fi

# --- Validate message ---
if [ -z "$MESSAGE" ]; then
  echo "ERROR: No message provided." >&2
  echo "Usage: openclaw_notify.sh [options] \"message\"" >&2
  exit 1
fi

# --- Rate limit check ---
if [ -n "$RATE_KEY" ]; then
  if ! check_rate_limit "$RATE_KEY"; then
    exit 0  # Silently skip (rate limited)
  fi
fi

# --- Dry run ---
if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY_RUN: would send notification:"
  echo "  Title:    $TITLE"
  echo "  Message:  $MESSAGE"
  echo "  Priority: $PRIORITY ($(priority_value "$PRIORITY"))"
  echo "  Rate Key: ${RATE_KEY:-none}"
  exit 0
fi

# --- Load secrets and send ---
if ! load_secrets; then
  exit 1
fi

PRIO_VAL="$(priority_value "$PRIORITY")"
SEND_TIMESTAMP="$(date +%s)"

# Secrets sent via stdin (heredoc) — NEVER in process argv.
# Uses JSON body to avoid --form-string exposing tokens in `ps`.
SEND_RC=0
# Escape JSON special characters in message and title
ESCAPED_TITLE="$(echo "$TITLE" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read().strip())[1:-1])' 2>/dev/null || echo "$TITLE")"
ESCAPED_MSG="$(echo "$MESSAGE" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read().strip())[1:-1])' 2>/dev/null || echo "$MESSAGE")"

$OPENCLAW_NOTIFY_CURL_CMD -sf -X POST \
  -H "Content-Type: application/json" \
  -d @- \
  https://api.pushover.net/1/messages.json >/dev/null 2>&1 <<SEND_JSON || SEND_RC=$?
{"token":"$APP_TOKEN","user":"$USER_KEY","title":"$ESCAPED_TITLE","message":"$ESCAPED_MSG","priority":$PRIO_VAL,"timestamp":$SEND_TIMESTAMP}
SEND_JSON

if [ "$SEND_RC" -ne 0 ]; then
  echo "ERROR: Pushover send failed (rc=$SEND_RC)" >&2
  exit 1
fi

# Mark rate-limit AFTER successful send (never suppress on delivery failure)
_mark_rate_sent "$RATE_KEY"

echo "Notification sent: $TITLE — $MESSAGE (priority=$PRIORITY)"

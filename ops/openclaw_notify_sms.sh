#!/usr/bin/env bash
# openclaw_notify_sms.sh — Send SMS notifications for OpenClaw events.
#
# Wraps the Python SMS module for outbound alert notifications.
# Called from doctor/guard/nightly when SMS is configured.
#
# Usage:
#   ./ops/openclaw_notify_sms.sh --event "DOCTOR_FAIL" --message "3 checks failed"
#   ./ops/openclaw_notify_sms.sh --event "GUARD_FAIL" --message "Remediation needed"
#   ./ops/openclaw_notify_sms.sh --event "NIGHTLY_FAIL" --message "Jobs failed"
#   ./ops/openclaw_notify_sms.sh --event "SIZE_CAP_WARN" --message "Review bundle too big"
#   ./ops/openclaw_notify_sms.sh --event "WORKFLOW_SUCCESS" --message "Snapshot complete"
#   ./ops/openclaw_notify_sms.sh --event "WORKFLOW_FAIL" --message "Harvest failed"
#   ./ops/openclaw_notify_sms.sh --dry-run --event "TEST" --message "test"
#   ./ops/openclaw_notify_sms.sh --test
#
# Secrets: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, SMS_ALLOWLIST
# Resolution: env → macOS Keychain → Linux file /etc/ai-ops-runner/secrets/
#
# Fail-closed: exits 0 if SMS not configured (graceful skip).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# --- Defaults ---
EVENT=""
MESSAGE=""
DRY_RUN=0
TEST_MODE=0
RATE_KEY=""

# --- Parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --event)    EVENT="$2"; shift 2 ;;
    --message)  MESSAGE="$2"; shift 2 ;;
    --rate-key) RATE_KEY="$2"; shift 2 ;;
    --dry-run)  DRY_RUN=1; shift ;;
    --test)     TEST_MODE=1; shift ;;
    -h|--help)
      echo "Usage: openclaw_notify_sms.sh --event EVENT --message MSG [--rate-key KEY] [--dry-run] [--test]"
      exit 0
      ;;
    *) echo "ERROR: Unknown flag: $1" >&2; exit 1 ;;
  esac
done

# --- Test mode ---
if [ "$TEST_MODE" -eq 1 ]; then
  echo "=== openclaw_notify_sms.sh — configuration test ==="
  python3 -m services.soma_kajabi_sync.sms test 2>&1 || {
    echo "  SMS not configured (non-fatal). Configure Twilio to enable."
    exit 0
  }
  exit 0
fi

# --- Validate args ---
if [ -z "$EVENT" ] || [ -z "$MESSAGE" ]; then
  echo "ERROR: --event and --message are required." >&2
  exit 1
fi

# --- Use rate-key = event name if not specified ---
if [ -z "$RATE_KEY" ]; then
  RATE_KEY="sms_$EVENT"
fi

# --- Send via Python SMS module ---
if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY_RUN: would send SMS alert"
  echo "  Event:   $EVENT"
  echo "  Message: $MESSAGE"
  echo "  Rate Key: $RATE_KEY"
  exit 0
fi

# Attempt SMS send — graceful skip if not configured
python3 -m services.soma_kajabi_sync.sms alert \
  --event "$EVENT" \
  --message "$MESSAGE" \
  --rate-key "$RATE_KEY" 2>&1 || {
  echo "  SMS send skipped or failed (non-fatal)."
  exit 0
}

#!/usr/bin/env bash
# openclaw_sms.sh — SMS integration driver for OpenClaw.
#
# Wraps the Python SMS module for shell-based invocations.
#
# Usage:
#   ./ops/openclaw_sms.sh send --to +15551234567 --message "Test"
#   ./ops/openclaw_sms.sh alert --event "DOCTOR_FAIL" --message "3 checks failed"
#   ./ops/openclaw_sms.sh test
#   ./ops/openclaw_sms.sh status
#
# Secrets: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, SMS_ALLOWLIST
# Resolution: env → macOS Keychain → Linux file /etc/ai-ops-runner/secrets/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# Route to the Python SMS module
exec python3 -m services.soma_kajabi_sync.sms "$@"

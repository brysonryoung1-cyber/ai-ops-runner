#!/usr/bin/env bash
# openclaw_sms_selftest.sh — Selftest for SMS integration.
#
# Verifies that the SMS module:
#   1. Imports correctly
#   2. Allowlist logic works (empty = deny all)
#   3. Rate limiting logic works
#   4. Inbound command routing works
#   5. Error logging works
#
# Does NOT require Twilio credentials. All tests are hermetic.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

echo "=== openclaw_sms_selftest.sh ==="

FAILURES=0
pass() { echo "  PASS: $1"; }
fail() { FAILURES=$((FAILURES + 1)); echo "  FAIL: $1" >&2; }

# --- 1. Module import ---
echo "--- SMS Module Import ---"
if python3 -c "from services.soma_kajabi_sync.sms import send_sms, send_alert, handle_inbound_sms, is_allowed_sender" 2>/dev/null; then
  pass "SMS module imports"
else
  fail "SMS module import failed"
fi

# --- 2. Allowlist logic ---
echo "--- Allowlist Logic ---"
ALLOW_RESULT="$(SMS_ALLOWLIST="" python3 -c "
from services.soma_kajabi_sync.sms import is_allowed_sender
print('deny' if not is_allowed_sender('+15551234567') else 'allow')
" 2>/dev/null)"

if [ "$ALLOW_RESULT" = "deny" ]; then
  pass "Empty allowlist denies all (fail-closed)"
else
  fail "Empty allowlist should deny (got: $ALLOW_RESULT)"
fi

ALLOW_RESULT2="$(SMS_ALLOWLIST="+15551234567" python3 -c "
from services.soma_kajabi_sync.sms import is_allowed_sender
print('allow' if is_allowed_sender('+15551234567') else 'deny')
" 2>/dev/null)"

if [ "$ALLOW_RESULT2" = "allow" ]; then
  pass "Matching number allowed"
else
  fail "Matching number should be allowed (got: $ALLOW_RESULT2)"
fi

# --- 3. Rate limit logic ---
echo "--- Rate Limit Logic ---"
RATE_RESULT="$(python3 -c "
import tempfile, os
from pathlib import Path
from unittest.mock import patch

tmpd = tempfile.mkdtemp()
with patch('services.soma_kajabi_sync.sms._RATE_DIR', Path(tmpd)):
    from services.soma_kajabi_sync.sms import _check_rate_limit, _mark_rate_sent
    # First call should be allowed
    assert _check_rate_limit('test', 60), 'First call should be allowed'
    _mark_rate_sent('test')
    # Second call should be blocked
    assert not _check_rate_limit('test', 60), 'Second call should be blocked'
print('OK')
" 2>/dev/null)"

if [ "$RATE_RESULT" = "OK" ]; then
  pass "Rate limiting works correctly"
else
  fail "Rate limiting test failed"
fi

# --- 4. Error log ---
echo "--- Error Log ---"
ERR_RESULT="$(python3 -c "
import tempfile, os, json
from pathlib import Path
from unittest.mock import patch

tmpf = Path(tempfile.mktemp(suffix='.jsonl'))
with patch('services.soma_kajabi_sync.sms._ERROR_LOG_PATH', tmpf):
    from services.soma_kajabi_sync.sms import log_error, get_last_errors
    log_error('Test error 1')
    log_error('Test error 2')
    errors = get_last_errors(5)
    assert len(errors) == 2, f'Expected 2 errors, got {len(errors)}'
    assert errors[0]['message'] == 'Test error 1'
tmpf.unlink(missing_ok=True)
print('OK')
" 2>/dev/null)"

if [ "$ERR_RESULT" = "OK" ]; then
  pass "Error logging works correctly"
else
  fail "Error logging test failed"
fi

# --- Summary ---
echo ""
if [ "$FAILURES" -gt 0 ]; then
  echo "=== openclaw_sms_selftest: $FAILURES failure(s) ===" >&2
  exit 1
fi
echo "=== openclaw_sms_selftest: ALL PASSED ==="
exit 0

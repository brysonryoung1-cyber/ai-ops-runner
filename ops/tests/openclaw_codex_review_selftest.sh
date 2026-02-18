#!/usr/bin/env bash
# openclaw_codex_review_selftest.sh — Hermetic tests for openclaw_codex_review.sh
#
# Tests script structure, security gates, and review bundle generation.
# NO real API calls. NO real secrets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OPS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REVIEW="$OPS_DIR/openclaw_codex_review.sh"

TESTS_PASSED=0
TESTS_FAILED=0
TESTS_RUN=0

pass() { TESTS_RUN=$((TESTS_RUN + 1)); TESTS_PASSED=$((TESTS_PASSED + 1)); echo "  PASS [$TESTS_RUN]: $1"; }
fail() { TESTS_RUN=$((TESTS_RUN + 1)); TESTS_FAILED=$((TESTS_FAILED + 1)); echo "  FAIL [$TESTS_RUN]: $1" >&2; }

echo "=== openclaw_codex_review_selftest.sh ==="

# --- Test 1: Script exists and is executable ---
if [ -x "$REVIEW" ]; then
  pass "openclaw_codex_review.sh exists and is executable"
else
  fail "openclaw_codex_review.sh not found or not executable"
fi

# --- Test 2: Help flag ---
OUTPUT="$(bash "$REVIEW" --help 2>&1)"
if echo "$OUTPUT" | grep -q "Usage"; then
  pass "Help flag shows usage"
else
  fail "Help flag missing usage"
fi

# --- Test 3: Script uses OpenAI API (not Codex CLI) ---
if grep -q "api.openai.com" "$REVIEW"; then
  pass "Script uses OpenAI API endpoint"
else
  fail "Script should use OpenAI API"
fi

# --- Test 4: Script gates on security regressions ---
if grep -q "public_binds" "$REVIEW"; then
  pass "Script checks for public bind regressions"
else
  fail "Script missing public bind security check"
fi

# --- Test 5: Script gates on allowlist bypass ---
if grep -q "allowlist_bypass" "$REVIEW"; then
  pass "Script checks for allowlist bypass"
else
  fail "Script missing allowlist bypass check"
fi

# --- Test 6: Script gates on key handling ---
if grep -q "key_handling" "$REVIEW"; then
  pass "Script checks for key handling regressions"
else
  fail "Script missing key handling check"
fi

# --- Test 7: Script gates on guard/doctor disablement ---
if grep -q "guard_doctor_intact" "$REVIEW"; then
  pass "Script checks for guard/doctor disablement"
else
  fail "Script missing guard/doctor check"
fi

# --- Test 8: Script gates on lockout risk ---
if grep -q "lockout_risk" "$REVIEW"; then
  pass "Script checks for lockout risk"
else
  fail "Script missing lockout risk check"
fi

# --- Test 9: Script supports --gate mode ---
if grep -q "\-\-gate\|GATE_MODE" "$REVIEW"; then
  pass "Script supports --gate mode"
else
  fail "Script missing --gate mode"
fi

# --- Test 10: Script writes verdict to artifacts ---
if grep -q "CODEX_VERDICT.json" "$REVIEW"; then
  pass "Script writes CODEX_VERDICT.json"
else
  fail "Script missing verdict file output"
fi

# --- Test 11: Script uses JSON response format ---
if grep -q "json_object\|response_format" "$REVIEW"; then
  pass "Script requests JSON response format"
else
  fail "Script should request JSON response format"
fi

# --- Test 12: Script loads OpenAI key via ensure_openai_key ---
if grep -q "ensure_openai_key" "$REVIEW"; then
  pass "Script sources ensure_openai_key.sh"
else
  fail "Script should source ensure_openai_key.sh"
fi

# --- Test 13: Script never prints raw API key ---
# Fail only on patterns that could expand or print the key (not literal env name in messages)
if grep -qE 'echo\s+\$OPENAI_API_KEY|echo\s+"\$OPENAI_API_KEY|print\s*\([^)]*api_key\s*\)' "$REVIEW" 2>/dev/null; then
  fail "Script may print raw API key"
else
  pass "Script does not print raw API key"
fi

# --- Test 14: Script uses set -euo pipefail ---
if head -20 "$REVIEW" | grep -q "set -euo pipefail"; then
  pass "Script uses set -euo pipefail"
else
  fail "Script missing set -euo pipefail"
fi

# --- Test 15: Review bundle script exists ---
if [ -x "$OPS_DIR/review_bundle.sh" ]; then
  pass "review_bundle.sh exists and is executable"
else
  fail "review_bundle.sh not found or not executable"
fi

# --- Test 16: Script handles SIZE_CAP ---
if grep -q "SIZE_CAP\|size.cap\|BUNDLE_RC.*6" "$REVIEW"; then
  pass "Script handles SIZE_CAP (exit 6)"
else
  fail "Script missing SIZE_CAP handling"
fi

# --- Summary ---
echo ""
echo "=== Codex Review Selftest: $TESTS_PASSED/$TESTS_RUN passed ==="
if [ "$TESTS_FAILED" -gt 0 ]; then
  echo "FAIL: $TESTS_FAILED test(s) failed" >&2
  exit 1
fi
echo "All tests passed."
exit 0

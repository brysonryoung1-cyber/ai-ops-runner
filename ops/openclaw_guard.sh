#!/usr/bin/env bash
# openclaw_guard.sh — Continuous regression guard for OpenClaw safety.
#
# Designed to run every 10 minutes via openclaw-guard.timer.
#
# Behavior:
#   1. Run openclaw_doctor.sh
#   2. If PASS → log PASS, exit 0
#   3. If FAIL →
#      a. Check: does Tailscale IPv4 exist?
#      b. Check: is sshd bound to 0.0.0.0:22 or [::]:22?
#      c. If BOTH → run openclaw_fix_ssh_tailscale_only.sh, then re-run doctor
#      d. If Tailscale is DOWN → do NOT touch sshd (avoid lockout)
#   4. Always append timestamped report to /var/log/openclaw_guard.log
#   5. Exit nonzero if still failing after remediation attempt
#
# CRITICAL SAFETY RULE:
#   If Tailscale is NOT up, NEVER rewrite sshd ListenAddress.
#   This prevents bricking remote access if Tailscale is temporarily down.
#
# Test mode: set OPENCLAW_GUARD_TEST_ROOT to redirect log path
#            set OPENCLAW_GUARD_TEST_MODE=1 to use stub scripts
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# --- Config ---
OPENCLAW_GUARD_TEST_ROOT="${OPENCLAW_GUARD_TEST_ROOT:-}"
OPENCLAW_GUARD_TEST_MODE="${OPENCLAW_GUARD_TEST_MODE:-0}"

if [ -n "$OPENCLAW_GUARD_TEST_ROOT" ]; then
  LOG_FILE="$OPENCLAW_GUARD_TEST_ROOT/openclaw_guard.log"
else
  LOG_FILE="/var/log/openclaw_guard.log"
fi

TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- Logging helper ---
log() {
  echo "$1"
  echo "[$TIMESTAMP] $1" >> "$LOG_FILE"
}

log_section() {
  echo "$1" >> "$LOG_FILE"
}

echo "=== openclaw_guard.sh ==="
echo "  Time: $TIMESTAMP"
echo "  Host: $(hostname)"
echo ""

log_section "--- guard run: $TIMESTAMP ---"

# ---------------------------------------------------------------------------
# Step 1: Run openclaw_doctor.sh
# ---------------------------------------------------------------------------
log "Step 1: Running openclaw_doctor.sh"
DOCTOR_RC=0
if [ "$OPENCLAW_GUARD_TEST_MODE" = "1" ] && [ -n "$OPENCLAW_GUARD_TEST_ROOT" ]; then
  # In test mode, run the stub doctor script
  DOCTOR_OUTPUT="$("$OPENCLAW_GUARD_TEST_ROOT/doctor_stub.sh" 2>&1)" || DOCTOR_RC=$?
else
  DOCTOR_OUTPUT="$(./ops/openclaw_doctor.sh 2>&1)" || DOCTOR_RC=$?
fi
echo "$DOCTOR_OUTPUT"

if [ "$DOCTOR_RC" -eq 0 ]; then
  log "RESULT: PASS (doctor 4/4)"
  log_section ""
  exit 0
fi

log "RESULT: FAIL (doctor rc=$DOCTOR_RC)"

# ---------------------------------------------------------------------------
# Step 2: Determine if safe remediation is possible
# ---------------------------------------------------------------------------
log "Step 2: Evaluating safe remediation"

# 2a. Is Tailscale up and returning an IPv4?
TS_IP=""
if command -v tailscale >/dev/null 2>&1; then
  TS_IP="$(tailscale ip -4 2>/dev/null | head -n1 | tr -d '[:space:]' || true)"
fi

if [ -z "$TS_IP" ]; then
  log "SKIP REMEDIATION: Tailscale IPv4 not available — will NOT touch sshd config"
  log "  Reason: Rewriting sshd ListenAddress without Tailscale could brick access."
  log "  Action: Guard will retry on next cycle. Fix Tailscale manually if persistent."
  log_section ""
  exit 1
fi

log "  Tailscale IPv4: $TS_IP"

# 2b. Is sshd bound to a public address (0.0.0.0:22 or [::]:22)?
SSHD_PUBLIC=false
if command -v ss >/dev/null 2>&1; then
  SSH_BINDS="$(ss -lntp 2>/dev/null | grep ':22 ' || true)"
  if echo "$SSH_BINDS" | grep -qE '0\.0\.0\.0:22|\[::\]:22|\*:22'; then
    SSHD_PUBLIC=true
  fi
fi

if [ "$SSHD_PUBLIC" = "false" ]; then
  log "SKIP SSH REMEDIATION: sshd is not bound to a public address"
  log "  Doctor failure is from a different cause. Manual investigation needed."
  log_section ""
  exit 1
fi

# ---------------------------------------------------------------------------
# Step 3: Safe remediation — both conditions met
# ---------------------------------------------------------------------------
log "Step 3: REMEDIATING — Tailscale is up ($TS_IP) and sshd is public-bound"

FIX_RC=0
if [ "$OPENCLAW_GUARD_TEST_MODE" = "1" ] && [ -n "$OPENCLAW_GUARD_TEST_ROOT" ]; then
  FIX_OUTPUT="$("$OPENCLAW_GUARD_TEST_ROOT/fix_stub.sh" 2>&1)" || FIX_RC=$?
else
  FIX_OUTPUT="$(sudo ./ops/openclaw_fix_ssh_tailscale_only.sh 2>&1)" || FIX_RC=$?
fi

echo "$FIX_OUTPUT" | tail -10
log_section "$FIX_OUTPUT"

if [ "$FIX_RC" -ne 0 ]; then
  log "REMEDIATION FAILED: openclaw_fix_ssh_tailscale_only.sh exited $FIX_RC"
  log_section ""
  exit 1
fi

log "REMEDIATION APPLIED: SSH fix completed (rc=0)"

# ---------------------------------------------------------------------------
# Step 4: Re-run doctor to confirm
# ---------------------------------------------------------------------------
log "Step 4: Re-running openclaw_doctor.sh (post-remediation)"
DOCTOR_RC2=0
if [ "$OPENCLAW_GUARD_TEST_MODE" = "1" ] && [ -n "$OPENCLAW_GUARD_TEST_ROOT" ]; then
  DOCTOR_OUTPUT2="$("$OPENCLAW_GUARD_TEST_ROOT/doctor_stub_post.sh" 2>&1)" || DOCTOR_RC2=$?
else
  DOCTOR_OUTPUT2="$(./ops/openclaw_doctor.sh 2>&1)" || DOCTOR_RC2=$?
fi
echo "$DOCTOR_OUTPUT2"

if [ "$DOCTOR_RC2" -eq 0 ]; then
  log "FINAL RESULT: PASS after remediation (doctor 4/4)"
  log_section ""
  exit 0
else
  log "FINAL RESULT: STILL FAILING after remediation (doctor rc=$DOCTOR_RC2)"
  log "  Manual investigation required."
  log_section ""
  exit 1
fi

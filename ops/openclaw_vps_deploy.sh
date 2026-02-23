#!/usr/bin/env bash
# openclaw_vps_deploy.sh — One-command full deploy to aiops-1 over Tailscale.
#
# Performs:
#   1. Pull origin/main (hard reset)
#   2. Rebuild docker compose stack
#   3. Run openclaw_heal.sh --notify
#   4. Run openclaw_doctor.sh (fail if any check fails)
#   5. Install/update guard timer (idempotent)
#   6. Build/start console via docker-compose.console.yml (idempotent)
#   7. Ensure console binds ONLY to 127.0.0.1:8787
#   8. Set up tailscale serve mapping (idempotent) — HTTPS 443 → http://127.0.0.1:8787
#   9. Print the phone URL (ts.net DNSName) and validate tailnet-only
#  10. Write deploy receipt artifact
#
# Usage:
#   ./ops/openclaw_vps_deploy.sh                    # Full deploy
#   ./ops/openclaw_vps_deploy.sh --dry-run           # Print plan, no execution
#
# Exit codes:
#   0 = deploy succeeded, all checks green
#   1 = any step failed (fail-closed)
#
# For testing: set OPENCLAW_VPS_DEPLOY_TEST_MODE=1 and OPENCLAW_VPS_DEPLOY_TEST_ROOT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- Config ---
VPS_HOST="${OPENCLAW_VPS_HOST:-root@100.123.61.57}"
VPS_DIR="/opt/ai-ops-runner"
SSH_OPTS="-o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
CONSOLE_PORT=8787
TS_DNS_NAME=""
DEPLOY_TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
RECEIPT_DIR="$ROOT_DIR/artifacts/deploy/${DEPLOY_TIMESTAMP}"

# Test mode support
TEST_MODE="${OPENCLAW_VPS_DEPLOY_TEST_MODE:-0}"
TEST_ROOT="${OPENCLAW_VPS_DEPLOY_TEST_ROOT:-}"

# Dry run
DRY_RUN=0

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      echo "Usage: openclaw_vps_deploy.sh [--dry-run]"
      echo "  Full deploy of OpenClaw to aiops-1 over Tailscale."
      exit 0
      ;;
    *) echo "ERROR: Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# --- Helpers ---
STEP=0
FAILURES=0
step() { STEP=$((STEP + 1)); echo ""; echo "==> Step $STEP: $1"; }
pass() { echo "  PASS: $1"; }
fail() { FAILURES=$((FAILURES + 1)); echo "  FAIL: $1" >&2; }

_ssh_cmd() {
  # Execute a command string on the remote host via heredoc (safe for spaces/operators)
  local cmd="$1"
  if [ "$TEST_MODE" = "1" ] && [ -n "$TEST_ROOT" ]; then
    echo "$cmd" | "$TEST_ROOT/ssh_stub.sh" bash
  else
    # shellcheck disable=SC2086
    ssh $SSH_OPTS "$VPS_HOST" bash <<REMOTE_CMD
$cmd
REMOTE_CMD
  fi
}

_ssh_script() {
  # Execute a script (from stdin) on the remote host
  if [ "$TEST_MODE" = "1" ] && [ -n "$TEST_ROOT" ]; then
    "$TEST_ROOT/ssh_stub.sh" bash
  else
    # shellcheck disable=SC2086
    ssh $SSH_OPTS "$VPS_HOST" bash
  fi
}

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  openclaw_vps_deploy.sh — Full Deploy to aiops-1           ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo "  Time:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  Target: $VPS_HOST:$VPS_DIR"
echo "  Mode:   $([ "$DRY_RUN" -eq 1 ] && echo 'DRY RUN' || echo 'LIVE')"
echo ""

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY RUN — would execute:"
  echo "  1. SSH to $VPS_HOST: git fetch + reset --hard origin/main"
  echo "  2. docker compose up -d --build"
  echo "  3. sudo ./ops/openclaw_heal.sh --notify"
  echo "  4. ./ops/openclaw_doctor.sh"
  echo "  5. sudo ./ops/openclaw_install_guard.sh"
  echo "  6. docker compose -f docker-compose.yml -f docker-compose.console.yml up -d --build"
  echo "  7. Verify console bind on 127.0.0.1:$CONSOLE_PORT"
  echo "  8. tailscale serve --bg --https=443 http://127.0.0.1:$CONSOLE_PORT"
  echo "  9. Print phone URL + validate tailnet-only"
  echo " 10. Write deploy receipt to $RECEIPT_DIR"
  exit 0
fi

# Temp dir for capturing output
DEPLOY_TMP="$(mktemp -d)"
trap 'rm -rf "$DEPLOY_TMP"' EXIT

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Pull origin/main (hard reset)
# ─────────────────────────────────────────────────────────────────────────────
step "Sync repo to origin/main"
_ssh_script <<REMOTE_SYNC
set -euo pipefail
if [ ! -d '${VPS_DIR}/.git' ]; then
  echo "  ERROR: ${VPS_DIR} is not a git repo" >&2
  exit 1
fi
cd '${VPS_DIR}'
git fetch origin main
git reset --hard origin/main
echo "  HEAD: \$(git rev-parse --short HEAD) (\$(git log -1 --format='%s'))"
REMOTE_SYNC
VPS_HEAD="$(_ssh_cmd "cd $VPS_DIR && git rev-parse --short HEAD" 2>/dev/null || echo "unknown")"
pass "Synced to origin/main ($VPS_HEAD)"

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Docker compose rebuild
# ─────────────────────────────────────────────────────────────────────────────
step "Docker compose rebuild"
_ssh_script <<REMOTE_DOCKER
set -euo pipefail
cd '${VPS_DIR}'
# Ensure secrets and config dirs exist (config for soma_kajabi_exit_node.txt)
sudo mkdir -p /etc/ai-ops-runner/secrets /etc/ai-ops-runner/config
sudo chown -R 1000:1000 /etc/ai-ops-runner/secrets 2>/dev/null || true
sudo chmod 750 /etc/ai-ops-runner/secrets
sudo chmod 755 /etc/ai-ops-runner/config
docker compose up -d --build 2>&1 | tail -10
echo "  Docker compose: rebuilt"
REMOTE_DOCKER
pass "Docker stack rebuilt"

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Heal (apply + verify + evidence)
# ─────────────────────────────────────────────────────────────────────────────
step "Run openclaw_heal.sh --notify"
HEAL_RC=0
_ssh_script <<'REMOTE_HEAL' || HEAL_RC=$?
set -euo pipefail
cd /opt/ai-ops-runner
sudo ./ops/openclaw_heal.sh --notify 2>&1 | tail -30
REMOTE_HEAL

if [ "$HEAL_RC" -eq 0 ]; then
  pass "Heal completed"
else
  fail "Heal FAILED (rc=$HEAL_RC)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Doctor verification
# ─────────────────────────────────────────────────────────────────────────────
step "Run openclaw_doctor.sh"
DOCTOR_RC=0
_ssh_script > "$DEPLOY_TMP/doctor.out" 2>&1 <<'REMOTE_DOCTOR' || DOCTOR_RC=$?
set -euo pipefail
cd /opt/ai-ops-runner
./ops/openclaw_doctor.sh
REMOTE_DOCTOR
DOCTOR_OUTPUT="$(cat "$DEPLOY_TMP/doctor.out")"
echo "$DOCTOR_OUTPUT"

if [ "$DOCTOR_RC" -eq 0 ]; then
  pass "Doctor all checks passed"
else
  fail "Doctor FAILED (rc=$DOCTOR_RC)"
fi

# --- Update project state on VPS (canonical brain + artifact snapshot) ---
step "Update project state (brain snapshot)"
OPENCLAW_DEPLOY_TIMESTAMP="$DEPLOY_TIMESTAMP" _ssh_script <<REMOTE_STATE
set -euo pipefail
cd '${VPS_DIR}'
if [ -f ops/update_project_state.py ]; then
  OPENCLAW_DEPLOY_TIMESTAMP='${DEPLOY_TIMESTAMP}' OPS_DIR="\$(pwd)/ops" python3 ops/update_project_state.py
  echo "  Project state updated; artifact written to artifacts/state/"
else
  echo "  WARN: ops/update_project_state.py not found"
fi
REMOTE_STATE
pass "Project state updated on VPS"

# ─────────────────────────────────────────────────────────────────────────────
# Step 6: Install/update guard timer
# ─────────────────────────────────────────────────────────────────────────────
step "Install/update guard timer (idempotent)"
GUARD_RC=0
_ssh_script <<'REMOTE_GUARD' || GUARD_RC=$?
set -euo pipefail
cd /opt/ai-ops-runner
sudo ./ops/openclaw_install_guard.sh 2>&1 | tail -15
REMOTE_GUARD

if [ "$GUARD_RC" -eq 0 ]; then
  pass "Guard timer installed/updated"
else
  fail "Guard timer install FAILED (rc=$GUARD_RC)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 7: Console build + start (idempotent, docker-compose)
# ─────────────────────────────────────────────────────────────────────────────
step "Build/start console (docker-compose, idempotent)"
CONSOLE_RC=0
_ssh_script <<'REMOTE_CONSOLE' || CONSOLE_RC=$?
set -euo pipefail
cd /opt/ai-ops-runner

# Load console token from file if it exists
CONSOLE_TOKEN=""
if [ -f /etc/ai-ops-runner/secrets/openclaw_console_token ]; then
  CONSOLE_TOKEN="$(cat /etc/ai-ops-runner/secrets/openclaw_console_token 2>/dev/null | tr -d '[:space:]')"
fi
export OPENCLAW_CONSOLE_TOKEN="$CONSOLE_TOKEN"

# Load admin token from canonical path (required for console→hostd auth)
ADMIN_TOKEN=""
if [ -f /etc/ai-ops-runner/secrets/openclaw_admin_token ]; then
  ADMIN_TOKEN="$(cat /etc/ai-ops-runner/secrets/openclaw_admin_token 2>/dev/null | tr -d '[:space:]')"
fi
if [ -z "$ADMIN_TOKEN" ]; then
  for f in /etc/ai-ops-runner/secrets/openclaw_console_token /etc/ai-ops-runner/secrets/openclaw_api_token /etc/ai-ops-runner/secrets/openclaw_token; do
    [ -f "$f" ] && ADMIN_TOKEN="$(cat "$f" 2>/dev/null | tr -d '[:space:]')" && [ -n "$ADMIN_TOKEN" ] && break
  done
fi
export OPENCLAW_ADMIN_TOKEN="$ADMIN_TOKEN"

# Trust Tailscale by default (private-only network)
export OPENCLAW_TRUST_TAILSCALE="${OPENCLAW_TRUST_TAILSCALE:-1}"

# Set build SHA from git rev-parse for console container
BUILD_SHA="$(cd /opt/ai-ops-runner && git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
export OPENCLAW_BUILD_SHA="$BUILD_SHA"

# Set AIOPS_HOST to this machine's Tailscale IPv4 (required for SSH exec)
AIOPS_HOST="$(tailscale ip -4 2>/dev/null | head -n1 | tr -d '[:space:]')"
if [ -z "$AIOPS_HOST" ]; then
  echo "  ERROR: Could not determine Tailscale IPv4 — cannot start console safely" >&2
  exit 1
fi
# Validate IPv4 format (digits and dots only, CGNAT 100.x range)
if ! echo "$AIOPS_HOST" | grep -qE '^100\.[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "  ERROR: AIOPS_HOST='$AIOPS_HOST' does not look like a Tailscale CGNAT IPv4" >&2
  exit 1
fi
export AIOPS_HOST

OPENCLAW_BUILD_SHA="$BUILD_SHA" docker compose -f docker-compose.yml -f docker-compose.console.yml up -d --build 2>&1 | tail -10
echo "  Console: built and started (build_sha=$BUILD_SHA)"
REMOTE_CONSOLE

if [ "$CONSOLE_RC" -eq 0 ]; then
  pass "Console started"
else
  fail "Console start FAILED (rc=$CONSOLE_RC)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 8: Verify console bind (ONLY 127.0.0.1:8787)
# ─────────────────────────────────────────────────────────────────────────────
step "Verify console binds to 127.0.0.1:$CONSOLE_PORT only"
sleep 2
# Capture ALL ss lines for the console port (not just first) to detect mixed binds
BIND_ALL="$(_ssh_cmd "ss -tlnp 2>/dev/null | grep ':${CONSOLE_PORT} ' || echo ''" 2>/dev/null || echo "")"

if [ -z "$BIND_ALL" ]; then
  fail "Console port $CONSOLE_PORT not listening (container may be starting)"
elif echo "$BIND_ALL" | grep -qE "0\.0\.0\.0:${CONSOLE_PORT}|\[::\]:${CONSOLE_PORT}|\*:${CONSOLE_PORT}"; then
  # Check for public bind FIRST (fail-closed: any public line = FAIL, even if localhost also present)
  fail "Console bound to a PUBLIC address — SECURITY VIOLATION"
elif echo "$BIND_ALL" | grep -q "127.0.0.1:${CONSOLE_PORT}"; then
  pass "Console bound to 127.0.0.1:$CONSOLE_PORT (private-only)"
else
  fail "Console bind check: unexpected bind addresses"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 9: Tailscale serve mapping (idempotent)
# ─────────────────────────────────────────────────────────────────────────────
step "Set up tailscale serve (HTTPS 443 → http://127.0.0.1:$CONSOLE_PORT)"
TS_SERVE_RC=0
_ssh_cmd "sudo tailscale serve --bg --https=443 http://127.0.0.1:${CONSOLE_PORT}" || TS_SERVE_RC=$?

if [ "$TS_SERVE_RC" -eq 0 ]; then
  pass "Tailscale serve configured"
else
  fail "Tailscale serve FAILED (rc=$TS_SERVE_RC)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 10: Print phone URL + validate tailnet-only
# ─────────────────────────────────────────────────────────────────────────────
step "Validate phone URL (tailnet-only)"
TS_DNS_NAME="$(_ssh_cmd 'tailscale status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get(\"Self\",{}).get(\"DNSName\",\"\").rstrip(\".\"))"' 2>/dev/null || echo "")"

if [ -z "$TS_DNS_NAME" ]; then
  TS_DNS_NAME="aiops-1.tailc75c62.ts.net"
  fail "Could not determine Tailscale DNS name (using fallback: $TS_DNS_NAME)"
fi

PHONE_URL="https://${TS_DNS_NAME}"

if echo "$TS_DNS_NAME" | grep -q '\.ts\.net'; then
  pass "Phone URL is tailnet-only: $PHONE_URL"
else
  fail "DNS name does not look like a ts.net domain: $TS_DNS_NAME"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 11: Deploy receipt
# ─────────────────────────────────────────────────────────────────────────────
step "Write deploy receipt"
mkdir -p "$RECEIPT_DIR"

# Capture VPS state for receipt
VPS_DOCTOR_SUMMARY="$(_ssh_cmd "cd $VPS_DIR && ./ops/openclaw_doctor.sh 2>&1 | tail -5" 2>/dev/null || echo "unavailable")"
VPS_GUARD_STATUS="$(_ssh_cmd "systemctl is-active openclaw-guard.timer 2>/dev/null || echo inactive" 2>/dev/null || echo "unavailable")"
VPS_TS_SERVE="$(_ssh_cmd "tailscale serve status 2>/dev/null || echo unavailable" 2>/dev/null || echo "unavailable")"
VPS_KEY_STATUS="$(_ssh_cmd "cd $VPS_DIR && python3 ops/openai_key.py status 2>/dev/null || echo unavailable" 2>/dev/null || echo "unavailable")"

# Write JSON receipt
python3 -c "
import json, sys
from datetime import datetime, timezone

receipt = {
    'deploy_timestamp': '${DEPLOY_TIMESTAMP}',
    'deploy_time_iso': datetime.now(timezone.utc).isoformat(),
    'target': '${VPS_HOST}',
    'vps_dir': '${VPS_DIR}',
    'vps_head': '${VPS_HEAD}',
    'deploy_sha': '${VPS_HEAD}',
    'console_build_sha': '${VPS_HEAD}',
    'phone_url': '${PHONE_URL}',
    'console_bind': '127.0.0.1:${CONSOLE_PORT}',
    'tailscale_serve': 'HTTPS:443 -> http://127.0.0.1:${CONSOLE_PORT}',
    'steps_total': ${STEP},
    'failures': ${FAILURES},
    'result': 'PASS' if ${FAILURES} == 0 else 'FAIL',
}
with open('${RECEIPT_DIR}/deploy_receipt.json', 'w') as f:
    json.dump(receipt, f, indent=2)
print('  Receipt JSON written')
"

# Human-readable receipt
cat > "$RECEIPT_DIR/RECEIPT.txt" <<EOF
OpenClaw VPS Deploy Receipt
═══════════════════════════
Time:     $(date -u +%Y-%m-%dT%H:%M:%SZ)
Target:   $VPS_HOST ($VPS_DIR)
VPS HEAD: $VPS_HEAD
Result:   $([ "$FAILURES" -eq 0 ] && echo "ALL PASSED" || echo "FAILURES: $FAILURES")

Phone URL:    $PHONE_URL
Console Bind: 127.0.0.1:$CONSOLE_PORT
TS Serve:     HTTPS:443 → http://127.0.0.1:$CONSOLE_PORT

Guard Timer:  $VPS_GUARD_STATUS
Key Status:   $(echo "$VPS_KEY_STATUS" | head -1)

Doctor Summary:
$VPS_DOCTOR_SUMMARY

Tailscale Serve Status:
$VPS_TS_SERVE
EOF

pass "Deploy receipt written to $RECEIPT_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# Final Summary
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
if [ "$FAILURES" -eq 0 ]; then
  echo "║  DEPLOY PASS — $STEP/$STEP steps succeeded                    ║"
  echo "║                                                              ║"
  echo "║  Phone URL: $PHONE_URL"
  echo "║  Receipt:   $RECEIPT_DIR"
  echo "╚══════════════════════════════════════════════════════════════╝"
  # Notify success
  if [ -x "$SCRIPT_DIR/openclaw_notify.sh" ]; then
    "$SCRIPT_DIR/openclaw_notify.sh" --title "OpenClaw Deploy" \
      --rate-key "deploy_pass" \
      "DEPLOY PASS ($VPS_HEAD) — $PHONE_URL" 2>/dev/null || true
  fi
  exit 0
else
  echo "║  DEPLOY FAIL — $FAILURES failure(s) in $STEP steps                ║"
  echo "║  Receipt: $RECEIPT_DIR"
  echo "╚══════════════════════════════════════════════════════════════╝"
  # Auto-collect diagnostics bundle on failure
  echo ""
  echo "==> Auto-collecting diagnostics bundle..."
  DIAG_DIR="$RECEIPT_DIR/diagnostics"
  if [ -n "$PHONE_URL" ] && [ "$PHONE_URL" != "https://" ]; then
    OPENCLAW_VERIFY_BASE_URL="$PHONE_URL" \
    OPENCLAW_ADMIN_TOKEN="${OPENCLAW_ADMIN_TOKEN:-}" \
    OPENCLAW_BUNDLE_DIR="$DIAG_DIR" \
    "$SCRIPT_DIR/support_bundle_collect_prod.sh" 2>/dev/null || echo "  WARNING: Diagnostics collection failed"
    echo "  Diagnostics: $DIAG_DIR"
  else
    echo "  WARNING: No phone URL available, skipping remote diagnostics"
  fi
  # Notify failure
  if [ -x "$SCRIPT_DIR/openclaw_notify.sh" ]; then
    "$SCRIPT_DIR/openclaw_notify.sh" --priority high --title "OpenClaw Deploy" \
      --rate-key "deploy_fail" \
      "DEPLOY FAIL: $FAILURES failure(s) in $STEP steps" 2>/dev/null || true
  fi
  exit 1
fi

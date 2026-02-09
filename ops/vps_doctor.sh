#!/usr/bin/env bash
# vps_doctor.sh — Remote health check for ai-ops-runner VPS
# Run from LOCAL machine. SSHes into VPS and verifies all components.
#
# Required env:
#   VPS_SSH_TARGET — e.g. runner@100.x.y.z
#
# Usage:
#   VPS_SSH_TARGET=runner@100.x.y.z ./ops/vps_doctor.sh
set -euo pipefail

VPS_SSH="${VPS_SSH_TARGET:?ERROR: Set VPS_SSH_TARGET=runner@host}"
REPO_DIR="/opt/ai-ops-runner"

ERRORS=0
WARNINGS=0

pass() { echo "  [OK]   $1"; }
fail() { echo "  [FAIL] $1" >&2; ERRORS=$((ERRORS + 1)); }
warn() { echo "  [WARN] $1" >&2; WARNINGS=$((WARNINGS + 1)); }

vssh() {
  ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "$VPS_SSH" "$@"
}

echo "=== vps_doctor.sh ==="
echo "  Target: $VPS_SSH"
echo ""

# --- Tailscale ---
echo "--- Tailscale ---"
TS_OUTPUT="$(vssh 'tailscale status 2>&1 | head -5' 2>/dev/null || echo 'FAIL')"
if echo "$TS_OUTPUT" | grep -qi "fail\|stopped\|not running"; then
  fail "Tailscale not running"
else
  pass "Tailscale connected"
  TS_IP="$(vssh 'tailscale ip -4 2>/dev/null || echo N/A')"
  echo "         IP: $TS_IP"
fi

# --- UFW ---
echo ""
echo "--- UFW ---"
UFW_STATUS="$(vssh 'sudo ufw status 2>/dev/null || echo inactive' 2>/dev/null)"
if echo "$UFW_STATUS" | grep -q "Status: active"; then
  pass "UFW active"
  # Check no public SSH
  if echo "$UFW_STATUS" | grep -q "22/tcp.*ALLOW.*Anywhere"; then
    warn "SSH (22/tcp) allowed from Anywhere (should be tailscale-only)"
  else
    pass "SSH restricted to Tailscale"
  fi
else
  fail "UFW not active"
fi

# --- Docker ---
echo ""
echo "--- Docker ---"
DOCKER_OK="$(vssh 'docker info >/dev/null 2>&1 && echo OK || echo FAIL' 2>/dev/null)"
if [ "$DOCKER_OK" = "OK" ]; then
  pass "Docker running"
else
  fail "Docker not running or user not in docker group"
fi

# --- Docker Compose services ---
echo ""
echo "--- Docker Compose ---"
COMPOSE_PS="$(vssh "cd $REPO_DIR && docker compose ps --format json 2>/dev/null" 2>/dev/null || echo "")"
if [ -n "$COMPOSE_PS" ]; then
  pass "docker compose ps returned data"
  # Count running services
  RUNNING="$(echo "$COMPOSE_PS" | python3 -c "
import sys, json
lines = sys.stdin.read().strip().split('\n')
running = sum(1 for l in lines if l.strip() and json.loads(l).get('State') == 'running')
total = len([l for l in lines if l.strip()])
print(f'{running}/{total}')
" 2>/dev/null || echo "?")"
  echo "         Services running: $RUNNING"
else
  fail "docker compose ps failed"
fi

# --- Port bindings (private-only verification) ---
echo ""
echo "--- Port Bindings (must be 127.0.0.1 or docker-internal only) ---"
PORT_CHECK="$(vssh 'ss -lntp 2>/dev/null | grep -E ":(8000|5432|6379) "' 2>/dev/null || echo "")"
if [ -n "$PORT_CHECK" ]; then
  # Check for 0.0.0.0 bindings (BAD)
  if echo "$PORT_CHECK" | grep -q "0.0.0.0"; then
    fail "Found 0.0.0.0 binding (public exposure!):"
    echo "$PORT_CHECK" | grep "0.0.0.0" | while read -r line; do echo "         $line"; done
  else
    pass "No public (0.0.0.0) port bindings"
  fi
  echo "$PORT_CHECK" | while read -r line; do echo "         $line"; done
else
  pass "No relevant ports exposed on host (docker-internal only)"
fi

# --- Systemd timers ---
echo ""
echo "--- Systemd Timers ---"
for timer in ai-ops-runner-update.timer ai-ops-runner-smoke.timer; do
  TIMER_STATUS="$(vssh "systemctl is-enabled $timer 2>/dev/null || echo disabled" 2>/dev/null)"
  if [ "$TIMER_STATUS" = "enabled" ]; then
    pass "$timer enabled"
    NEXT="$(vssh "systemctl show $timer --property=NextElapseUSecRealtime --value 2>/dev/null" 2>/dev/null || echo "?")"
    echo "         Next run: $NEXT"
  else
    fail "$timer not enabled ($TIMER_STATUS)"
  fi
done

# --- Main service ---
echo ""
echo "--- Main Service ---"
SVC_STATUS="$(vssh 'systemctl is-enabled ai-ops-runner.service 2>/dev/null || echo disabled' 2>/dev/null)"
if [ "$SVC_STATUS" = "enabled" ]; then
  pass "ai-ops-runner.service enabled"
else
  warn "ai-ops-runner.service not enabled ($SVC_STATUS)"
fi

# --- Repo state ---
echo ""
echo "--- Repository ---"
REPO_HEAD="$(vssh "cd $REPO_DIR && git rev-parse --short HEAD 2>/dev/null || echo NONE" 2>/dev/null)"
REPO_BRANCH="$(vssh "cd $REPO_DIR && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo NONE" 2>/dev/null)"
if [ "$REPO_HEAD" != "NONE" ]; then
  pass "Repo present at $REPO_DIR"
  echo "         HEAD: $REPO_HEAD  Branch: $REPO_BRANCH"
else
  fail "Repo not found at $REPO_DIR"
fi

# --- API health ---
echo ""
echo "--- API Health ---"
API_HEALTH="$(vssh 'curl -sf http://127.0.0.1:8000/healthz 2>/dev/null || echo FAIL' 2>/dev/null)"
if echo "$API_HEALTH" | grep -q '"ok"'; then
  pass "API /healthz: $API_HEALTH"
else
  fail "API /healthz: $API_HEALTH"
fi

# --- GitHub DNS ---
echo ""
echo "--- GitHub DNS ---"
GH_DNS="$(vssh 'getent hosts github.com 2>/dev/null | head -1 || echo FAIL' 2>/dev/null)"
if [ "$GH_DNS" != "FAIL" ] && [ -n "$GH_DNS" ]; then
  pass "GitHub DNS: $GH_DNS"
else
  fail "GitHub DNS resolution failed"
fi

# --- Log directory ---
echo ""
echo "--- Logs ---"
LOG_CHECK="$(vssh 'ls -la /var/log/ai-ops-runner/ 2>/dev/null | head -5 || echo NONE' 2>/dev/null)"
if [ "$LOG_CHECK" != "NONE" ]; then
  pass "/var/log/ai-ops-runner/ exists"
  SMOKE_COUNT="$(vssh 'ls /var/log/ai-ops-runner/smoke-*.log 2>/dev/null | wc -l' 2>/dev/null || echo "0")"
  echo "         Smoke logs: $SMOKE_COUNT"
else
  warn "/var/log/ai-ops-runner/ not found"
fi

# --- Summary ---
echo ""
echo "=== Summary ==="
if [ "$ERRORS" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
  echo "  All checks passed!"
  exit 0
elif [ "$ERRORS" -eq 0 ]; then
  echo "  $WARNINGS warning(s), 0 errors"
  exit 0
else
  echo "  $ERRORS error(s), $WARNINGS warning(s)"
  exit 1
fi

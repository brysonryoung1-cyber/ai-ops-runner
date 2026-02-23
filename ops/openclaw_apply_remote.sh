#!/usr/bin/env bash
# openclaw_apply_remote.sh — One-command "apply + verify" for OpenClaw on a remote VPS.
#
# Usage: ./ops/openclaw_apply_remote.sh [host]
#
# Default host: root@100.123.61.57 (aiops-1 via Tailscale)
#
# APPLY_MODE detection (no self-SSH):
#   - If target IP matches this host's Tailscale IPv4 → local mode (run steps directly, no SSH)
#   - If target is remote → ssh_target mode (requires OPENCLAW_VPS_SSH_IDENTITY; fail-closed if missing)
#
# What it does (on remote or local):
#   1. cd /opt/ai-ops-runner && git fetch origin main && git reset --hard origin/main
#   2. docker compose up -d --build
#   3. sudo ./ops/openclaw_fix_ssh_tailscale_only.sh
#   4. ./ops/openclaw_doctor.sh
#   5. Soma smoke test
#   6. ss -lntp | egrep '(:22 |:8000 |:53 )' || true
#
# Exit codes:
#   0 = all steps passed, doctor 4/4
#   1 = one or more steps failed
#   255 = APPLY_SSH_KEY_MISSING (remote mode, key not configured)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- Default host and SSH ---
# Optional: OPENCLAW_VPS_SSH_IDENTITY=/path/to/deploy_key (required for ssh_target mode)
# Optional: OPENCLAW_VPS_SSH_HOST overrides default (e.g. root@100.123.61.57)
VPS_HOST="${OPENCLAW_VPS_SSH_HOST:-${1:-root@100.123.61.57}}"
VPS_DIR="/opt/ai-ops-runner"

# Extract target IP from VPS_HOST (user@ip or user@hostname or just ip)
TARGET_IP=""
case "$VPS_HOST" in
  *@*) TARGET_IP="${VPS_HOST#*@}" ;;
  *)   TARGET_IP="$VPS_HOST" ;;
esac

# Detect local mode: target IP matches this host's Tailscale IPv4
LOCAL_TAILSCALE_IP=""
if command -v tailscale >/dev/null 2>&1; then
  LOCAL_TAILSCALE_IP="$(tailscale ip -4 2>/dev/null || true)"
fi
APPLY_MODE="ssh_target"
if [ -n "$LOCAL_TAILSCALE_IP" ] && [ "$TARGET_IP" = "$LOCAL_TAILSCALE_IP" ]; then
  APPLY_MODE="local"
fi

# ssh_target mode: require SSH key (fail-closed)
SSH_OPTS="-o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
if [ "$APPLY_MODE" = "ssh_target" ]; then
  if [ -z "${OPENCLAW_VPS_SSH_IDENTITY:-}" ] || [ ! -r "${OPENCLAW_VPS_SSH_IDENTITY}" ]; then
    echo "=== openclaw_apply_remote.sh: APPLY_SSH_KEY_MISSING ===" >&2
    echo "  Target is remote ($VPS_HOST). SSH key required." >&2
    echo "  Set OPENCLAW_VPS_SSH_IDENTITY to path of deploy key (readable by hostd user)." >&2
    echo "  One external action: Run openclaw_apply_remote_setup_ssh.sh on ship host, install pubkey on target." >&2
    exit 255
  fi
  SSH_OPTS="$SSH_OPTS -o IdentitiesOnly=yes -i ${OPENCLAW_VPS_SSH_IDENTITY}"
fi

echo "=== openclaw_apply_remote.sh ==="
echo "  Time:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  Host:   $VPS_HOST"
echo "  Mode:   $APPLY_MODE"
echo "  Remote: $VPS_DIR"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Sync repo to origin/main
# ---------------------------------------------------------------------------
echo "==> Step 1: Sync repo to origin/main"
if [ "$APPLY_MODE" = "local" ]; then
  (
    set -euo pipefail
    if [ ! -d "${VPS_DIR}/.git" ]; then
      echo "  ERROR: ${VPS_DIR} does not exist or is not a git repo." >&2
      echo "  Run vps_bootstrap.sh first." >&2
      exit 1
    fi
    cd "$VPS_DIR"
    git fetch origin main
    git reset --hard origin/main
    echo "  HEAD: $(git rev-parse --short HEAD) ($(git log -1 --format='%s'))"
  )
else
  ssh $SSH_OPTS "$VPS_HOST" bash <<REMOTE_SYNC
set -euo pipefail
if [ ! -d '${VPS_DIR}/.git' ]; then
  echo "  ERROR: ${VPS_DIR} does not exist or is not a git repo." >&2
  echo "  Run vps_bootstrap.sh first." >&2
  exit 1
fi
cd '${VPS_DIR}'
git fetch origin main
git reset --hard origin/main
echo "  HEAD: \$(git rev-parse --short HEAD) (\$(git log -1 --format='%s'))"
REMOTE_SYNC
fi
echo ""

# ---------------------------------------------------------------------------
# Step 2: Docker compose up
# ---------------------------------------------------------------------------
echo "==> Step 2: docker compose up -d --build"
if [ "$APPLY_MODE" = "local" ]; then
  (cd "$VPS_DIR" && docker compose up -d --build 2>&1 | tail -5)
  echo "  Docker compose: done"
else
  ssh $SSH_OPTS "$VPS_HOST" bash <<REMOTE_DOCKER
set -euo pipefail
cd '${VPS_DIR}'
docker compose up -d --build 2>&1 | tail -5
echo "  Docker compose: done"
REMOTE_DOCKER
fi
echo ""

# ---------------------------------------------------------------------------
# Step 3: Apply SSH Tailscale-only fix (best-effort; continue on failure)
# ---------------------------------------------------------------------------
echo "==> Step 3: Apply SSH Tailscale-only fix"
SSH_FIX_RC=0
if [ "$APPLY_MODE" = "local" ]; then
  (cd "$VPS_DIR" && (
    if command -v tailscale >/dev/null 2>&1 && tailscale ip -4 >/dev/null 2>&1; then
      sudo ./ops/openclaw_fix_ssh_tailscale_only.sh 2>&1 | tail -20
    else
      echo "  WARNING: Tailscale not up — skipping SSH fix to avoid lockout"
      echo "  This is safe: the fix will run on next guard cycle when Tailscale recovers."
    fi
  )) || SSH_FIX_RC=$?
else
  ssh $SSH_OPTS "$VPS_HOST" bash <<REMOTE_SSH_FIX || SSH_FIX_RC=$?
set -euo pipefail
cd '${VPS_DIR}'
if command -v tailscale >/dev/null 2>&1 && tailscale ip -4 >/dev/null 2>&1; then
  sudo ./ops/openclaw_fix_ssh_tailscale_only.sh 2>&1 | tail -20
else
  echo "  WARNING: Tailscale not up — skipping SSH fix to avoid lockout"
  echo "  This is safe: the fix will run on next guard cycle when Tailscale recovers."
fi
REMOTE_SSH_FIX
fi
[ "$SSH_FIX_RC" -ne 0 ] && echo "  WARNING: Step 3 failed (rc=$SSH_FIX_RC); continuing (doctor/ports still run)."
echo ""

# ---------------------------------------------------------------------------
# Step 4: Run openclaw_doctor
# ---------------------------------------------------------------------------
echo "==> Step 4: Run openclaw_doctor.sh"
DOCTOR_RC=0
if [ "$APPLY_MODE" = "local" ]; then
  (cd "$VPS_DIR" && ./ops/openclaw_doctor.sh) || DOCTOR_RC=$?
else
  ssh $SSH_OPTS "$VPS_HOST" bash <<REMOTE_DOCTOR || DOCTOR_RC=$?
set -euo pipefail
cd '${VPS_DIR}'
./ops/openclaw_doctor.sh
REMOTE_DOCTOR
fi
echo ""

# ---------------------------------------------------------------------------
# Step 5: Soma smoke test
# ---------------------------------------------------------------------------
echo "==> Step 5: Soma smoke test"
SOMA_SMOKE_RC=0
if [ "$APPLY_MODE" = "local" ]; then
  (cd "$VPS_DIR" && (
    if [ -f ./ops/soma_smoke.sh ]; then
      chmod +x ./ops/soma_smoke.sh
      ./ops/soma_smoke.sh 2>&1 | tail -30
    else
      echo "  SKIP: soma_smoke.sh not found"
    fi
  )) || SOMA_SMOKE_RC=$?
else
  ssh $SSH_OPTS "$VPS_HOST" bash <<REMOTE_SOMA_SMOKE || SOMA_SMOKE_RC=$?
set -euo pipefail
cd '${VPS_DIR}'
if [ -f ./ops/soma_smoke.sh ]; then
  chmod +x ./ops/soma_smoke.sh
  ./ops/soma_smoke.sh 2>&1 | tail -30
else
  echo "  SKIP: soma_smoke.sh not found"
fi
REMOTE_SOMA_SMOKE
fi
echo ""

if [ "$SOMA_SMOKE_RC" -ne 0 ]; then
  echo "  WARNING: Soma smoke failed (rc=$SOMA_SMOKE_RC) — non-fatal" >&2
fi

# ---------------------------------------------------------------------------
# Step 6: Port proof
# ---------------------------------------------------------------------------
echo "==> Step 6: Port listeners (proof)"
if [ "$APPLY_MODE" = "local" ]; then
  ss -lntp 2>/dev/null | grep -E '(:22 |:8000 |:53 )' || echo "  (no matching listeners found)"
else
  ssh $SSH_OPTS "$VPS_HOST" bash <<'REMOTE_PORTS'
ss -lntp 2>/dev/null | grep -E '(:22 |:8000 |:53 )' || echo "  (no matching listeners found)"
REMOTE_PORTS
fi
echo ""

# ---------------------------------------------------------------------------
# Final status
# ---------------------------------------------------------------------------
if [ "$DOCTOR_RC" -eq 0 ]; then
  echo "=== openclaw_apply_remote.sh: ALL PASSED ==="
  exit 0
else
  echo "=== openclaw_apply_remote.sh: DOCTOR FAILED (rc=$DOCTOR_RC) ===" >&2
  exit 1
fi

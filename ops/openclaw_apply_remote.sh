#!/usr/bin/env bash
# openclaw_apply_remote.sh — One-command "apply + verify" for OpenClaw on a remote VPS.
#
# Usage: ./ops/openclaw_apply_remote.sh [host]
#
# Default host: root@100.123.61.57 (aiops-1 via Tailscale)
#
# What it does (on remote):
#   1. cd /opt/ai-ops-runner && git fetch origin main && git reset --hard origin/main
#   2. docker compose up -d --build
#   3. sudo ./ops/openclaw_fix_ssh_tailscale_only.sh
#   4. ./ops/openclaw_doctor.sh
#   5. ss -lntp | egrep '(:22 |:8000 |:53 )' || true
#
# Exit codes:
#   0 = all steps passed, doctor 4/4
#   1 = one or more steps failed
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- Default host ---
VPS_HOST="${1:-root@100.123.61.57}"
VPS_DIR="/opt/ai-ops-runner"
SSH_OPTS="-o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -o BatchMode=yes"

echo "=== openclaw_apply_remote.sh ==="
echo "  Time:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  Host:   $VPS_HOST"
echo "  Remote: $VPS_DIR"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Sync repo to origin/main
# ---------------------------------------------------------------------------
echo "==> Step 1: Sync repo to origin/main"
# shellcheck disable=SC2086
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
echo ""

# ---------------------------------------------------------------------------
# Step 2: Docker compose up
# ---------------------------------------------------------------------------
echo "==> Step 2: docker compose up -d --build"
# shellcheck disable=SC2086
ssh $SSH_OPTS "$VPS_HOST" bash <<REMOTE_DOCKER
set -euo pipefail
cd '${VPS_DIR}'
docker compose up -d --build 2>&1 | tail -5
echo "  Docker compose: done"
REMOTE_DOCKER
echo ""

# ---------------------------------------------------------------------------
# Step 3: Apply SSH Tailscale-only fix
# ---------------------------------------------------------------------------
echo "==> Step 3: Apply SSH Tailscale-only fix"
# shellcheck disable=SC2086
ssh $SSH_OPTS "$VPS_HOST" bash <<REMOTE_SSH_FIX
set -euo pipefail
cd '${VPS_DIR}'
# Only run the fix if tailscale is up — fail-closed but do not brick access
if command -v tailscale >/dev/null 2>&1 && tailscale ip -4 >/dev/null 2>&1; then
  sudo ./ops/openclaw_fix_ssh_tailscale_only.sh 2>&1 | tail -20
else
  echo "  WARNING: Tailscale not up — skipping SSH fix to avoid lockout"
  echo "  This is safe: the fix will run on next guard cycle when Tailscale recovers."
fi
REMOTE_SSH_FIX
echo ""

# ---------------------------------------------------------------------------
# Step 4: Run openclaw_doctor
# ---------------------------------------------------------------------------
echo "==> Step 4: Run openclaw_doctor.sh"
DOCTOR_RC=0
# shellcheck disable=SC2086
ssh $SSH_OPTS "$VPS_HOST" bash <<REMOTE_DOCTOR || DOCTOR_RC=$?
set -euo pipefail
cd '${VPS_DIR}'
./ops/openclaw_doctor.sh
REMOTE_DOCTOR
echo ""

# ---------------------------------------------------------------------------
# Step 5: Port proof
# ---------------------------------------------------------------------------
echo "==> Step 5: Port listeners (proof)"
# shellcheck disable=SC2086
ssh $SSH_OPTS "$VPS_HOST" bash <<'REMOTE_PORTS'
ss -lntp 2>/dev/null | grep -E '(:22 |:8000 |:53 )' || echo "  (no matching listeners found)"
REMOTE_PORTS
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

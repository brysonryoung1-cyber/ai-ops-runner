#!/usr/bin/env bash
# vps_apply_aiops1.sh — Idempotent deploy to aiops-1 VPS.
#
# What it does:
#   1. Ensures /opt/ai-ops-runner exists and is on origin/main
#   2. docker compose up -d --build
#   3. Installs/enables systemd units + timers
#   4. Configures Tailscale Serve for tailnet-only API access (non-fatal)
#   5. Runs smoke test
#   6. Submits ORB review_bundle + doctor jobs and prints proof
#
# Usage: ./ops/vps_apply_aiops1.sh
# Requires: SSH access to root@100.123.61.57 (Tailscale)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

VPS_HOST="root@100.123.61.57"
VPS_DIR="/opt/ai-ops-runner"
REPO_URL="https://github.com/brysonryoung1-cyber/ai-ops-runner.git"

SSH_OPTS="-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"

echo "=== vps_apply_aiops1.sh ==="
echo "  VPS:  $VPS_HOST"
echo "  Dir:  $VPS_DIR"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Ensure repo is up-to-date on VPS
# ---------------------------------------------------------------------------
echo "==> Step 1: Sync repo to origin/main"
# shellcheck disable=SC2086
ssh $SSH_OPTS "$VPS_HOST" bash <<REMOTE_STEP1
set -euo pipefail
if [ ! -d '${VPS_DIR}/.git' ]; then
  echo '  Cloning repo...'
  git clone '${REPO_URL}' '${VPS_DIR}'
fi
cd '${VPS_DIR}'
git fetch origin
git checkout main
# Intentionally destructive: converge VPS to exact origin/main state.
# Do NOT use on hosts with manual hotfixes — those would be lost.
git reset --hard origin/main
echo "  HEAD: \$(git rev-parse --short HEAD)"
REMOTE_STEP1
echo ""

# ---------------------------------------------------------------------------
# Step 2: Docker compose up
# ---------------------------------------------------------------------------
echo "==> Step 2: docker compose up -d --build"
# shellcheck disable=SC2086
ssh $SSH_OPTS "$VPS_HOST" bash <<REMOTE_STEP2
set -euo pipefail
cd '${VPS_DIR}'
docker compose up -d --build
REMOTE_STEP2
echo ""

# ---------------------------------------------------------------------------
# Step 3: Install systemd units + timers
# ---------------------------------------------------------------------------
echo "==> Step 3: Install systemd units"
SYSTEMD_SRC="$ROOT_DIR/ops/systemd"
SYSTEMD_DEST="/etc/systemd/system"

# Copy unit files to VPS
for unit in "$SYSTEMD_SRC"/*.service "$SYSTEMD_SRC"/*.timer; do
  [ -f "$unit" ] || continue
  echo "  Copying $(basename "$unit")..."
  # shellcheck disable=SC2086
  scp -q $SSH_OPTS "$unit" "$VPS_HOST:$SYSTEMD_DEST/$(basename "$unit")"
done

# Reload and enable
# shellcheck disable=SC2086
ssh $SSH_OPTS "$VPS_HOST" bash <<'REMOTE_STEP3'
set -euo pipefail
systemctl daemon-reload

# Enable main service
systemctl enable ai-ops-runner.service

# Enable timers (services are triggered by timers, not enabled directly)
systemctl enable --now ai-ops-runner-health.timer
systemctl enable --now ai-ops-orb-daily.timer
systemctl enable --now ai-ops-artifacts-prune.timer

echo '  Timers active:'
systemctl list-timers --no-pager | grep ai-ops || true
REMOTE_STEP3
echo ""

# ---------------------------------------------------------------------------
# Step 4: Tailscale Serve (tailnet-only API access) — NON-FATAL
# ---------------------------------------------------------------------------
echo "==> Step 4: Configure Tailscale Serve (non-fatal)"
# shellcheck disable=SC2086
ssh $SSH_OPTS "$VPS_HOST" bash <<'REMOTE_STEP4' || echo "  WARNING: Tailscale Serve setup failed (non-fatal). Enable Serve at https://login.tailscale.com"
set -euo pipefail
if ! command -v tailscale &>/dev/null; then
  echo '  tailscale not found, skipping'
  exit 0
fi
# Check if Serve is available
if ! tailscale serve status &>/dev/null; then
  echo '  Tailscale Serve not enabled on this tailnet.'
  echo '  Enable at: https://login.tailscale.com (Serve feature)'
  exit 0
fi
tailscale serve reset 2>/dev/null || true
tailscale serve --bg --https=443 http://127.0.0.1:8000 || {
  echo "  tailscale serve command failed (Serve may not be enabled on tailnet)"
  exit 0
}
echo '  Tailscale Serve configured: https://<tailnet-hostname>:443 -> 127.0.0.1:8000'
tailscale serve status 2>/dev/null || true
REMOTE_STEP4
echo ""

# ---------------------------------------------------------------------------
# Step 5: Wait for health + smoke test
# ---------------------------------------------------------------------------
echo "==> Step 5: Smoke test"
# shellcheck disable=SC2086
ssh $SSH_OPTS "$VPS_HOST" bash <<REMOTE_STEP5
set -euo pipefail
cd '${VPS_DIR}'

# Wait for API
for i in \$(seq 1 30); do
  if curl -sf http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    echo '  API healthy'
    break
  fi
  if [ "\$i" -eq 30 ]; then
    echo 'ERROR: API not healthy after 30s' >&2
    docker compose logs test_runner_api | tail -20
    exit 1
  fi
  sleep 1
done

# Submit local_echo smoke job
RESP=\$(curl -sf -X POST http://127.0.0.1:8000/jobs \
  -H 'Content-Type: application/json' \
  -d '{"job_type":"local_echo","repo_name":"smoke-test","remote_url":"https://github.com/octocat/Hello-World.git","sha":"7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"}')
JOB_ID=\$(echo "\$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "  smoke job_id=\$JOB_ID"

for i in \$(seq 1 60); do
  STATUS=\$(curl -s http://127.0.0.1:8000/jobs/\$JOB_ID | \
    python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  case "\$STATUS" in
    success) echo '  smoke: PASSED'; break ;;
    failure|error|timeout) echo "  smoke: FAILED (status=\$STATUS)" >&2; exit 1 ;;
    *) sleep 2 ;;
  esac
done
REMOTE_STEP5
echo ""

# ---------------------------------------------------------------------------
# Step 6: Submit ORB review_bundle + doctor and collect proof
# ---------------------------------------------------------------------------
echo "==> Step 6: ORB jobs + proof"
# shellcheck disable=SC2086
ssh $SSH_OPTS "$VPS_HOST" bash <<'REMOTE_STEP6'
set -euo pipefail
cd /opt/ai-ops-runner
API_BASE='http://127.0.0.1:8000'
ORB_URL='git@github.com:brysonryoung1-cyber/algo-nt8-orb.git'

# Resolve HEAD
ORB_SHA=$(git ls-remote "$ORB_URL" HEAD 2>/dev/null | cut -f1 || true)
if [ -z "$ORB_SHA" ]; then
  echo '  WARNING: Cannot resolve ORB HEAD (network/key issue?)'
  echo '  Skipping ORB job submission.'
  exit 0
fi
echo "  ORB HEAD: $ORB_SHA"

# Submit review_bundle
RB_RESP=$(curl -sf -X POST "$API_BASE/jobs" \
  -H 'Content-Type: application/json' \
  -d "{\"job_type\":\"orb_review_bundle\",\"repo_name\":\"algo-nt8-orb\",\"remote_url\":\"$ORB_URL\",\"sha\":\"$ORB_SHA\"}")
RB_JID=$(echo "$RB_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "  review_bundle job_id=$RB_JID"

# Submit doctor
DOC_RESP=$(curl -sf -X POST "$API_BASE/jobs" \
  -H 'Content-Type: application/json' \
  -d "{\"job_type\":\"orb_doctor\",\"repo_name\":\"algo-nt8-orb\",\"remote_url\":\"$ORB_URL\",\"sha\":\"$ORB_SHA\"}")
DOC_JID=$(echo "$DOC_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "  doctor job_id=$DOC_JID"

# Wait for both
for JID in "$RB_JID" "$DOC_JID"; do
  for i in $(seq 1 120); do
    STATUS=$(curl -s "$API_BASE/jobs/$JID" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
    case "$STATUS" in
      success|failure|error|timeout) echo "  job $JID: $STATUS"; break ;;
      *) sleep 5 ;;
    esac
  done
done

echo ''
echo '--- PROOF ---'
echo ''

# docker ps health
echo '==> docker ps:'
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

echo ''
echo '==> Network binds (no public 8000):'
ss -lntup 2>/dev/null | grep 8000 || echo '  (port 8000 not in ss — bound via docker network)'

echo ''
echo '==> Artifact dirs:'
echo "  review_bundle: ./artifacts/$RB_JID"
echo "  doctor:        ./artifacts/$DOC_JID"
[ -f "./artifacts/$RB_JID/REVIEW_BUNDLE.txt" ] && echo '  REVIEW_BUNDLE.txt: present' || echo '  REVIEW_BUNDLE.txt: pending'
[ -f "./artifacts/$DOC_JID/artifact.json" ] && echo '  artifact.json: present' || echo '  artifact.json: pending'

echo ''
echo '==> Systemd timers:'
systemctl list-timers --no-pager | grep ai-ops || true

echo ''
echo '==> Tailscale Serve status:'
tailscale serve status 2>/dev/null || echo '  (not configured or not enabled)'
REMOTE_STEP6

echo ""
echo "=== vps_apply_aiops1.sh COMPLETE ==="

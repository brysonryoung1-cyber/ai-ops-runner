#!/usr/bin/env bash
# vps_bootstrap.sh — Idempotent VPS bootstrap for ai-ops-runner (private-only)
# Run from LOCAL machine. SSHes into the VPS and configures everything.
#
# Required env:
#   VPS_SSH_TARGET  — e.g. runner@100.x.y.z or runner@vps-host
# Optional env:
#   TAILSCALE_AUTHKEY — Tailscale auth key (only needed for first-time setup)
#   REPO_BRANCH       — branch to deploy (default: main)
#
# Usage:
#   VPS_SSH_TARGET=runner@100.x.y.z TAILSCALE_AUTHKEY=tskey-... ./ops/vps_bootstrap.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

VPS_SSH="${VPS_SSH_TARGET:?ERROR: Set VPS_SSH_TARGET=runner@host}"
TS_KEY="${TAILSCALE_AUTHKEY:-}"
BRANCH="${REPO_BRANCH:-main}"
REPO_URL="https://github.com/brysonryoung1-cyber/ai-ops-runner.git"
REPO_DIR="/opt/ai-ops-runner"
LOG_DIR="/var/log/ai-ops-runner"

echo "============================================"
echo "  ai-ops-runner VPS bootstrap (private-only)"
echo "============================================"
echo "  Target:  $VPS_SSH"
echo "  Branch:  $BRANCH"
echo "  Repo:    $REPO_URL"
echo ""

# --- SSH helper (accept new host keys, 15s timeout) ---
vssh() {
  ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "$VPS_SSH" "$@"
}

# ========================================================
# STEP 1: Prerequisites (docker, git, ufw)
# ========================================================
echo "==> [1/8] Installing prerequisites..."
vssh bash <<'REMOTE_PREREQ'
set -euo pipefail
echo "--- Checking prerequisites ---"

install_pkg() {
  if command -v "$1" &>/dev/null; then
    echo "  $1: already installed ($(command -v "$1"))"
    return 0
  fi
  echo "  $1: installing..."
  return 1
}

# Docker
if ! install_pkg docker; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo systemctl enable --now docker
fi

# Docker Compose plugin
if docker compose version &>/dev/null; then
  echo "  docker compose: $(docker compose version --short 2>/dev/null || echo 'OK')"
else
  echo "  docker compose plugin: installing..."
  sudo apt-get update -qq
  sudo apt-get install -y -qq docker-compose-plugin 2>/dev/null || {
    ARCH="$(uname -m)"
    sudo mkdir -p /usr/local/lib/docker/cli-plugins
    COMPOSE_VER="$(curl -fsSL https://api.github.com/repos/docker/compose/releases/latest | grep -oP '"tag_name": "\K[^"]+')"
    sudo curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VER}/docker-compose-linux-${ARCH}" \
      -o /usr/local/lib/docker/cli-plugins/docker-compose
    sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
  }
fi

# Git
install_pkg git || { sudo apt-get update -qq && sudo apt-get install -y -qq git; }

# UFW
install_pkg ufw || { sudo apt-get update -qq && sudo apt-get install -y -qq ufw; }

# Ensure runner user is in docker group
if id -nG | grep -qw docker; then
  echo "  docker group: $(whoami) is a member"
else
  echo "  docker group: adding $(whoami)..."
  sudo usermod -aG docker "$(whoami)"
  echo "  NOTE: docker group added. May need re-login for full effect."
fi

echo "  Prerequisites OK."
REMOTE_PREREQ

# ========================================================
# STEP 2: Tailscale
# ========================================================
echo ""
echo "==> [2/8] Configuring Tailscale..."
if [ -n "$TS_KEY" ]; then
  # Pass authkey via stdin to avoid it appearing in process list
  echo "$TS_KEY" | vssh bash <<'REMOTE_TS'
set -euo pipefail
TS_KEY="$(cat)"

# Install tailscale if missing
if ! command -v tailscale &>/dev/null; then
  echo "  Installing Tailscale..."
  curl -fsSL https://tailscale.com/install.sh | sudo sh
fi

# Bring up tailscale (--ssh enables Tailscale SSH, --reset re-auths if needed)
echo "  Bringing Tailscale up..."
sudo tailscale up --authkey "$TS_KEY" --ssh --accept-routes --reset

echo "  Tailscale status:"
tailscale status | head -5
echo "  Tailscale IP: $(tailscale ip -4 2>/dev/null || echo 'N/A')"
REMOTE_TS
else
  echo "  TAILSCALE_AUTHKEY not set — assuming Tailscale already configured."
  vssh bash <<'REMOTE_TS_CHECK'
if command -v tailscale &>/dev/null; then
  echo "  Tailscale status:"
  tailscale status | head -3 2>/dev/null || echo "  WARNING: tailscale status failed"
  echo "  Tailscale IP: $(tailscale ip -4 2>/dev/null || echo 'N/A')"
else
  echo "  WARNING: Tailscale not installed. Set TAILSCALE_AUTHKEY to install."
fi
REMOTE_TS_CHECK
fi

# ========================================================
# STEP 3: UFW (deny all public, allow tailscale only)
# ========================================================
echo ""
echo "==> [3/8] Configuring UFW (private-only)..."
vssh bash <<'REMOTE_UFW'
set -euo pipefail

# Check tailscale is up before locking down — safety gate
if ! tailscale status &>/dev/null; then
  echo "  ERROR: Tailscale not connected. Refusing to configure UFW."
  echo "  Fix Tailscale first to avoid lockout."
  exit 1
fi

echo "  Configuring firewall (deny public, allow tailscale)..."
sudo ufw --force reset >/dev/null 2>&1
sudo ufw default deny incoming >/dev/null
sudo ufw default allow outgoing >/dev/null

# Allow ALL traffic on Tailscale interface (SSH, HTTP, etc.)
sudo ufw allow in on tailscale0 >/dev/null

# Allow Tailscale WireGuard UDP (required for Tailscale connectivity)
sudo ufw allow 41641/udp >/dev/null

sudo ufw --force enable >/dev/null
echo "  UFW configured:"
sudo ufw status numbered | head -15
REMOTE_UFW

# ========================================================
# STEP 4: GitHub DNS check
# ========================================================
echo ""
echo "==> [4/8] Verifying GitHub connectivity..."
vssh bash <<'REMOTE_DNS'
set -euo pipefail
if getent hosts github.com >/dev/null 2>&1; then
  echo "  GitHub DNS: OK ($(getent hosts github.com | head -1))"
else
  echo "  GitHub DNS: FAILED — attempting fix..."
  # Add Google/Cloudflare DNS if not present
  grep -q "nameserver 1.1.1.1" /etc/resolv.conf 2>/dev/null || {
    echo "nameserver 1.1.1.1" | sudo tee -a /etc/resolv.conf >/dev/null
    echo "nameserver 8.8.8.8" | sudo tee -a /etc/resolv.conf >/dev/null
  }
  if getent hosts github.com >/dev/null 2>&1; then
    echo "  GitHub DNS: FIXED"
  else
    echo "  ERROR: Cannot resolve github.com. Check network." >&2
    exit 1
  fi
fi

# Verify git clone connectivity
if git ls-remote https://github.com/brysonryoung1-cyber/ai-ops-runner.git HEAD >/dev/null 2>&1; then
  echo "  Git HTTPS: OK"
else
  echo "  WARNING: Cannot reach ai-ops-runner repo (may be private)"
fi
REMOTE_DNS

# ========================================================
# STEP 5: Clone/pull repo
# ========================================================
echo ""
echo "==> [5/8] Setting up repository..."
vssh bash -s -- "$REPO_URL" "$REPO_DIR" "$BRANCH" <<'REMOTE_REPO'
set -euo pipefail
REPO_URL="$1"
REPO_DIR="$2"
BRANCH="$3"

if [ ! -d "$REPO_DIR/.git" ]; then
  echo "  Cloning $REPO_URL -> $REPO_DIR..."
  sudo mkdir -p "$REPO_DIR"
  sudo chown "$(whoami):$(whoami)" "$REPO_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
else
  echo "  Pulling latest in $REPO_DIR..."
  cd "$REPO_DIR"
  git fetch origin "$BRANCH"
  git reset --hard "origin/$BRANCH"
fi

# Create required directories
mkdir -p "$REPO_DIR/artifacts" "$REPO_DIR/repos"
sudo mkdir -p /var/log/ai-ops-runner
sudo chown "$(whoami):$(whoami)" /var/log/ai-ops-runner

echo "  Repo HEAD: $(cd "$REPO_DIR" && git rev-parse --short HEAD)"
echo "  Branch: $(cd "$REPO_DIR" && git rev-parse --abbrev-ref HEAD)"
REMOTE_REPO

# ========================================================
# STEP 6: Docker Compose up
# ========================================================
echo ""
echo "==> [6/8] Starting Docker Compose..."
vssh bash -s -- "$REPO_DIR" <<'REMOTE_DOCKER'
set -euo pipefail
cd "$1"

# Use sg to pick up docker group if freshly added in this session
if sg docker -c "docker compose version" &>/dev/null; then
  sg docker -c "docker compose up -d --build"
else
  docker compose up -d --build
fi

echo ""
echo "  Docker services:"
docker compose ps 2>/dev/null || sg docker -c "docker compose ps"
REMOTE_DOCKER

# ========================================================
# STEP 7: Systemd units + timers
# ========================================================
echo ""
echo "==> [7/8] Installing systemd units + timers..."
vssh bash -s -- "$REPO_DIR" <<'REMOTE_SYSTEMD'
set -euo pipefail
REPO_DIR="$1"
RUNNER_USER="$(whoami)"

# --- ai-ops-runner.service (main compose stack) ---
sudo tee /etc/systemd/system/ai-ops-runner.service >/dev/null <<UNIT
[Unit]
Description=ai-ops-runner Docker Compose stack
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=${RUNNER_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=/usr/bin/docker compose up -d --build
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
UNIT

# --- ai-ops-runner-update.service (review-gated self-update) ---
sudo tee /etc/systemd/system/ai-ops-runner-update.service >/dev/null <<UNIT
[Unit]
Description=ai-ops-runner review-gated self-update
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${RUNNER_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${REPO_DIR}/ops/vps_self_update.sh
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ai-ops-runner-update
UNIT

# --- ai-ops-runner-update.timer (every 15 min) ---
sudo tee /etc/systemd/system/ai-ops-runner-update.timer >/dev/null <<UNIT
[Unit]
Description=ai-ops-runner update timer (every 15 min)

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
UNIT

# --- ai-ops-runner-smoke.service (daily smoke test) ---
sudo tee /etc/systemd/system/ai-ops-runner-smoke.service >/dev/null <<UNIT
[Unit]
Description=ai-ops-runner daily smoke test
After=ai-ops-runner.service docker.service

[Service]
Type=oneshot
User=${RUNNER_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=/bin/bash -c '${REPO_DIR}/ops/runner_smoke.sh 2>&1 | tee /var/log/ai-ops-runner/smoke-\$(date +%%Y%%m%%d).log'
Environment=API_BASE=http://127.0.0.1:8000
StandardOutput=journal
StandardError=journal
SyslogIdentifier=ai-ops-runner-smoke
UNIT

# --- ai-ops-runner-smoke.timer (daily at 06:00 UTC) ---
sudo tee /etc/systemd/system/ai-ops-runner-smoke.timer >/dev/null <<UNIT
[Unit]
Description=ai-ops-runner smoke test timer (daily 06:00 UTC)

[Timer]
OnCalendar=*-*-* 06:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
UNIT

# Reload and enable
sudo systemctl daemon-reload
sudo systemctl enable ai-ops-runner.service
sudo systemctl enable --now ai-ops-runner-update.timer
sudo systemctl enable --now ai-ops-runner-smoke.timer

echo "  Systemd units installed."
echo ""
echo "  Timers:"
systemctl list-timers --no-pager ai-ops-runner-* 2>/dev/null || \
  systemctl list-timers --no-pager | grep ai-ops-runner || true
echo ""
echo "  Services:"
systemctl is-enabled ai-ops-runner.service 2>/dev/null && echo "  ai-ops-runner.service: enabled" || true
systemctl is-enabled ai-ops-runner-update.timer 2>/dev/null && echo "  ai-ops-runner-update.timer: enabled" || true
systemctl is-enabled ai-ops-runner-smoke.timer 2>/dev/null && echo "  ai-ops-runner-smoke.timer: enabled" || true
REMOTE_SYSTEMD

# ========================================================
# STEP 8: Tailscale serve + smoke test
# ========================================================
echo ""
echo "==> [8/8] Setting up Tailscale serve + running smoke test..."
vssh bash -s -- "$REPO_DIR" <<'REMOTE_FINAL'
set -euo pipefail
REPO_DIR="$1"
cd "$REPO_DIR"

# Expose API via Tailscale serve (HTTPS on tailnet, proxying to localhost:8000)
if command -v tailscale &>/dev/null && tailscale status &>/dev/null; then
  echo "  Setting up tailscale serve (localhost:8000 -> tailnet HTTPS)..."
  sudo tailscale serve --bg --https=443 http://127.0.0.1:8000 2>/dev/null || \
    sudo tailscale serve --bg 8000 2>/dev/null || \
    echo "  WARNING: tailscale serve setup failed (may need manual config)"
fi

# Run smoke test
echo ""
echo "  Running smoke test..."
export API_BASE="http://127.0.0.1:8000"
./ops/runner_smoke.sh

# Verify private-only: show listening ports
echo ""
echo "  === Port binding verification ==="
ss -lntp 2>/dev/null | grep -E ':(8000|5432|6379) ' || echo "  (no matching ports in ss output)"
echo ""
echo "  === Tailscale serve status ==="
sudo tailscale serve status 2>/dev/null || echo "  (tailscale serve status not available)"
REMOTE_FINAL

echo ""
echo "============================================"
echo "  Bootstrap COMPLETE"
echo "============================================"
echo "  VPS:      $VPS_SSH"
echo "  Repo:     $REPO_DIR (branch: $BRANCH)"
echo "  API:      127.0.0.1:8000 (+ tailscale serve)"
echo "  Timers:   update (15min), smoke (daily 06:00 UTC)"
echo ""
echo "  Next: ./ops/vps_doctor.sh   — verify health"
echo "        ./ops/vps_deploy.sh   — re-deploy"
echo "============================================"

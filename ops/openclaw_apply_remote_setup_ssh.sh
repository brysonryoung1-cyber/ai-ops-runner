#!/usr/bin/env bash
# openclaw_apply_remote_setup_ssh.sh — One-time setup on the SHIP HOST so Apply OpenClaw (Remote) works.
#
# Run this ON THE MACHINE THAT RUNS HOSTD (ship host), e.g. the VPS where HQ runs or the box that has
# network reachability to the Apply target. Ensures:
#   1) A dedicated deploy key exists at /etc/ai-ops-runner/secrets/openclaw_ssh/vps_deploy_ed25519
#   2) The public key is installed on the target root@TARGET_HOST (default root@100.123.61.57)
#   3) hostd is configured with OPENCLAW_VPS_SSH_IDENTITY via /etc/ai-ops-runner/secrets/openclaw_hostd.env
#   4) SSH proof with the deploy key succeeds.
#
# Usage: sudo ./ops/openclaw_apply_remote_setup_ssh.sh [target_user@target_host]
#   Default target: root@100.123.61.57
#
# If you have NO existing access to the target, the script STOPS after Phase 2 and prints the exact
# one-liner to run ON THE TARGET to install the public key. No private key is ever printed.
set -euo pipefail

TARGET_SPEC="${1:-root@100.123.61.57}"
KEY_DIR="/etc/ai-ops-runner/secrets/openclaw_ssh"
KEY_PATH="$KEY_DIR/vps_deploy_ed25519"
HOSTD_ENV_FILE="/etc/ai-ops-runner/secrets/openclaw_hostd.env"
REPO_ROOT="${OPENCLAW_REPO_ROOT:-/opt/ai-ops-runner}"

echo "=== openclaw_apply_remote_setup_ssh.sh ==="
echo "  Target: $TARGET_SPEC"
echo "  Key path: $KEY_PATH"
echo "  Repo root: $REPO_ROOT"
echo ""

# ---------------------------------------------------------------------------
# Phase 0 — Identify ship host and confirm failing SSH
# ---------------------------------------------------------------------------
echo "==> Phase 0: Ship host and SSH check"
SHIP_HOST="$(hostname -s 2>/dev/null || echo 'unknown')"
echo "  Ship host = $SHIP_HOST"

if [ ! -d "$REPO_ROOT/.git" ]; then
  echo "  WARNING: Repo not found at $REPO_ROOT (expected on ship host). Continuing anyway." >&2
fi

echo -n "  SSH BatchMode to $TARGET_SPEC: "
if ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET_SPEC" "echo ok" 2>/dev/null; then
  echo "  (already works; will still configure deploy key for hostd)"
else
  echo "  FAIL (exit 255 / Permission denied expected). Deploy key will fix this."
fi
echo ""

# ---------------------------------------------------------------------------
# Phase 1 — Create dedicated deploy key (root-only perms)
# ---------------------------------------------------------------------------
echo "==> Phase 1: Deploy key"
sudo mkdir -p "$KEY_DIR"
sudo chmod 700 "$KEY_DIR"

if [ ! -f "$KEY_PATH" ]; then
  sudo ssh-keygen -t ed25519 -f "$KEY_PATH" -N "" -C "openclaw-apply@$SHIP_HOST"
  echo "  Created new keypair at $KEY_PATH"
else
  echo "  Key already exists at $KEY_PATH"
fi
sudo chmod 600 "$KEY_PATH" "$KEY_PATH.pub"
echo "  Public key (safe to share):"
sudo cat "$KEY_PATH.pub"
echo ""

# ---------------------------------------------------------------------------
# Phase 2 — Install public key on target (try SSH, then Tailscale SSH, else print one-liner)
# ---------------------------------------------------------------------------
echo "==> Phase 2: Install public key on target"
PUB="$(sudo cat "$KEY_PATH.pub")"
INSTALLED=0

# Attempt A: direct SSH (existing key/agent)
if ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET_SPEC" "mkdir -p ~/.ssh && chmod 700 ~/.ssh" 2>/dev/null; then
  echo "$PUB" | ssh "$TARGET_SPEC" "cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys" 2>/dev/null && INSTALLED=1
fi

# Attempt B: Tailscale SSH
if [ "$INSTALLED" -eq 0 ] && command -v tailscale >/dev/null 2>&1; then
  if tailscale ssh "$TARGET_SPEC" "mkdir -p ~/.ssh && chmod 700 ~/.ssh" 2>/dev/null; then
    echo "$PUB" | tailscale ssh "$TARGET_SPEC" "cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys" 2>/dev/null && INSTALLED=1
  fi
fi

# Attempt C: deploy key (after key was installed from Mac/target once; append is idempotent)
if [ "$INSTALLED" -eq 0 ] && [ -r "$KEY_PATH" ]; then
  if ssh -i "$KEY_PATH" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 "$TARGET_SPEC" "mkdir -p ~/.ssh && chmod 700 ~/.ssh" 2>/dev/null; then
    echo "$PUB" | ssh -i "$KEY_PATH" -o IdentitiesOnly=yes "$TARGET_SPEC" "cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys" 2>/dev/null && INSTALLED=1
  fi
fi

if [ "$INSTALLED" -eq 0 ]; then
  echo "  No existing SSH or Tailscale SSH access to $TARGET_SPEC."
  echo "  (1) Public key (safe to share):"
  echo "  $PUB"
  echo "  (2) On the TARGET HOST run (paste the key above where indicated):"
  echo "  mkdir -p /root/.ssh && chmod 700 /root/.ssh && echo '<PASTE_PUB_KEY_ABOVE>' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys"
  echo ""
  echo "  Then re-run this script on the ship host to complete Phase 3–4."
  exit 0
fi
echo "  Public key installed on target."
echo ""

# ---------------------------------------------------------------------------
# Phase 3 — Configure hostd to use the identity
# ---------------------------------------------------------------------------
echo "==> Phase 3: Configure hostd"
sudo mkdir -p "$(dirname "$HOSTD_ENV_FILE")"
sudo chmod 700 "$(dirname "$HOSTD_ENV_FILE")"
{
  echo "OPENCLAW_VPS_SSH_IDENTITY=$KEY_PATH"
  echo "OPENCLAW_VPS_SSH_HOST=$TARGET_SPEC"
} | sudo tee "$HOSTD_ENV_FILE" >/dev/null
sudo chmod 600 "$HOSTD_ENV_FILE"
echo "  Wrote $HOSTD_ENV_FILE"

# Ensure systemd unit has EnvironmentFile (re-run installer from repo if present)
if [ -f "$REPO_ROOT/ops/install_openclaw_hostd.sh" ]; then
  (cd "$REPO_ROOT" && sudo "$REPO_ROOT/ops/install_openclaw_hostd.sh") || true
else
  sudo systemctl daemon-reload
  sudo systemctl restart openclaw-hostd.service 2>/dev/null || echo "  (openclaw-hostd not installed yet; install it so hostd picks up env)"
fi
echo ""

# ---------------------------------------------------------------------------
# Phase 3 continued — Prove SSH with deploy key
# ---------------------------------------------------------------------------
echo "==> Phase 3: SSH proof with deploy key"
PROOF_OUT=""
PROOF_RC=0
PROOF_OUT=$(ssh -i "$KEY_PATH" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "$TARGET_SPEC" "echo OK_FROM_DEPLOY_KEY" 2>&1) || PROOF_RC=$?
if [ "$PROOF_RC" -eq 0 ] && [ "$PROOF_OUT" = "OK_FROM_DEPLOY_KEY" ]; then
  echo "  SSH proof: $PROOF_OUT (PASS)"
else
  echo "  SSH proof FAILED (rc=$PROOF_RC). stderr: $PROOF_OUT" >&2
  exit 1
fi
echo ""

# ---------------------------------------------------------------------------
# Phase 4 — Report
# ---------------------------------------------------------------------------
echo "=== Report ==="
echo "  Ship host = $SHIP_HOST"
echo "  Key path = $KEY_PATH"
echo "  Public key installed on target = yes"
echo "  SSH proof command output = $PROOF_OUT"
echo "  OPENCLAW_VPS_SSH_IDENTITY set via = $HOSTD_ENV_FILE"
echo "  Services restarted = openclaw-hostd (if installed)"
echo ""
echo "  Next: In HQ click Actions → Apply OpenClaw (Remote) once."
echo "=== Done ==="

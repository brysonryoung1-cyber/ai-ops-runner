#!/usr/bin/env bash
# vps_deploy.sh — Deploy ai-ops-runner to VPS (bootstrap + verify)
# Wrapper: runs bootstrap then doctor.
#
# Required env:
#   VPS_SSH_TARGET — e.g. runner@100.x.y.z
# Optional env:
#   TAILSCALE_AUTHKEY — only for first-time Tailscale setup
#   REPO_BRANCH       — branch to deploy (default: main)
#
# Usage:
#   VPS_SSH_TARGET=runner@100.x.y.z ./ops/vps_deploy.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== vps_deploy.sh ==="
echo ""

# Step 1: Bootstrap (idempotent)
"$SCRIPT_DIR/vps_bootstrap.sh"

echo ""
echo "==> Post-deploy health check..."
echo ""

# Step 2: Doctor (verify everything is healthy)
"$SCRIPT_DIR/vps_doctor.sh"

echo ""
echo "=== vps_deploy.sh COMPLETE ==="

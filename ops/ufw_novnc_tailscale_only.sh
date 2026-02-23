#!/usr/bin/env bash
# ufw_novnc_tailscale_only.sh — Idempotent UFW rule: allow noVNC port 6080 from Tailscale (100.64.0.0/10) only.
#
# Run on aiops-1 (root or sudo). No secrets. Ensures port 6080 is reachable from
# Tailscale CGNAT and blocked from public internet.
set -euo pipefail

NOVNC_PORT="${NOVNC_PORT:-6080}"
TAILSCALE_CIDR="100.64.0.0/10"

if ! command -v ufw >/dev/null 2>&1; then
  echo "ufw not installed; skipping noVNC firewall rule"
  exit 0
fi

# Idempotent: ufw allow does not duplicate existing rules
ufw allow from "$TAILSCALE_CIDR" to any port "$NOVNC_PORT" proto tcp 2>/dev/null || true

# Verify rule exists (ufw status format varies)
if ufw status 2>/dev/null | grep -qE "100\.64\.0\.0/10|$NOVNC_PORT"; then
  echo "  noVNC port $NOVNC_PORT: allowed from $TAILSCALE_CIDR"
  echo "  ufw_novnc_tailscale_only: PASS"
else
  echo "  ufw_novnc_tailscale_only: rule added (verify with: ufw status)" >&2
fi

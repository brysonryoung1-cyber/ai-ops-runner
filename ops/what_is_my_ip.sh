#!/usr/bin/env bash
# what_is_my_ip.sh — Show public egress IP (for exit-node proof).
# Safe: uses public IP endpoint only. Logs IP and timestamp.
set -euo pipefail

# Use a well-known public IP service (no auth, no secrets)
URL="${WHAT_IS_MY_IP_URL:-https://api.ipify.org}"
IP=""
if command -v curl &>/dev/null; then
  IP="$(curl -fsS --max-time 10 "$URL" 2>/dev/null || true)"
elif command -v wget &>/dev/null; then
  IP="$(wget -qO- --timeout=10 "$URL" 2>/dev/null || true)"
fi
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u)"
if [[ -n "$IP" ]]; then
  echo "{\"ip\":\"$IP\",\"timestamp\":\"$TS\"}"
  if command -v logger &>/dev/null; then
    logger -t what_is_my_ip -- "ip=$IP timestamp=$TS"
  fi
else
  echo "{\"ip\":null,\"timestamp\":\"$TS\",\"error\":\"could not resolve\"}"
  exit 1
fi

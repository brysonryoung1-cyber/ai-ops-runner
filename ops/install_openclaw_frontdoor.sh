#!/usr/bin/env bash
# install_openclaw_frontdoor.sh — Install Caddy frontdoor reverse proxy.
#
# Ensures Caddy is installed (apt), copies Caddyfile, installs systemd unit.
# Frontdoor binds 127.0.0.1:8788. Tailscale Serve forwards all traffic here.
#
# Run: sudo ./ops/install_openclaw_frontdoor.sh
# Idempotent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CADDYFILE="$ROOT_DIR/ops/caddy/Caddyfile.frontdoor"
UNIT_SRC="$ROOT_DIR/ops/systemd/openclaw-frontdoor.service"
UNIT_DST="/etc/systemd/system/openclaw-frontdoor.service"

# Disable default caddy.service (binds *:80; we use openclaw-frontdoor on 127.0.0.1:8788)
systemctl stop caddy.service 2>/dev/null || true
systemctl disable caddy.service 2>/dev/null || true
systemctl mask caddy.service 2>/dev/null || true
# Prevent Caddy from loading default /etc/caddy/Caddyfile (which binds :80)
[ -f /etc/caddy/Caddyfile ] && mv /etc/caddy/Caddyfile /etc/caddy/Caddyfile.disabled 2>/dev/null || true

# Ensure Caddy is installed
if ! command -v caddy >/dev/null 2>&1; then
  echo "Installing Caddy..."
  apt-get update -qq
  apt-get install -y -qq caddy 2>/dev/null || {
    echo "Caddy not in apt. Trying official install..." >&2
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null || true
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list 2>/dev/null || true
    apt-get update -qq && apt-get install -y -qq caddy 2>/dev/null || {
      echo "ERROR: Could not install Caddy. Install manually: apt install caddy" >&2
      exit 1
    }
  }
  echo "  Caddy installed: $(caddy version 2>/dev/null | head -1)"
fi

[ ! -f "$CADDYFILE" ] && { echo "ERROR: Caddyfile not found: $CADDYFILE" >&2; exit 1; }
[ ! -f "$UNIT_SRC" ] && { echo "ERROR: Unit not found: $UNIT_SRC" >&2; exit 1; }

# Generate Tailscale TLS cert for direct TLS termination in Caddy.
# Caddy terminates TLS on port 8443; Tailscale Serve TCP-proxies 443→8443.
# This bypasses Tailscale Serve's HTTPS reverse proxy which strips WebSocket upgrade headers.
TS_HOSTNAME="aiops-1.tailc75c62.ts.net"
if command -v tailscale >/dev/null 2>&1; then
  TS_HOSTNAME="$(tailscale status --json 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print((d.get('Self') or {}).get('DNSName', '').rstrip('.') or 'aiops-1.tailc75c62.ts.net')
except: print('aiops-1.tailc75c62.ts.net')
" 2>/dev/null)" || TS_HOSTNAME="aiops-1.tailc75c62.ts.net"
fi
mkdir -p /etc/ssl/tailscale
if ! tailscale cert --cert-file /etc/ssl/tailscale/aiops-1.crt --key-file /etc/ssl/tailscale/aiops-1.key "$TS_HOSTNAME" 2>/dev/null; then
  echo "WARNING: tailscale cert failed; TLS listener may not work" >&2
fi
echo "  TLS cert: /etc/ssl/tailscale/aiops-1.{crt,key}"

# Substitute ROOT_DIR in unit
sed "s|/opt/ai-ops-runner|$ROOT_DIR|g" "$UNIT_SRC" | tee "$UNIT_DST" >/dev/null
systemctl daemon-reload
systemctl enable openclaw-frontdoor.service
systemctl start openclaw-frontdoor.service 2>/dev/null || systemctl restart openclaw-frontdoor.service
echo "openclaw-frontdoor: installed and started (127.0.0.1:8788 + TLS on 8443)"

# Configure Tailscale Serve: TCP mode on 443 → Caddy TLS on 8443
tailscale serve reset 2>/dev/null || true
tailscale serve --bg --tcp=443 "tcp://127.0.0.1:8443" 2>/dev/null || true
echo "  tailscale serve: TCP 443 → 127.0.0.1:8443 (Caddy terminates TLS)"
exit 0

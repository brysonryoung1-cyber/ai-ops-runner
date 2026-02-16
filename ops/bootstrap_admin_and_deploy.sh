#!/usr/bin/env bash
# bootstrap_admin_and_deploy.sh — Ensure admin token on aiops-1, then run deploy_and_verify.
#
# Runs FROM local machine, SSHs to aiops-1. On aiops-1:
#   1. Discover existing token (openclaw_admin_token, openclaw_console_token, openclaw_api_token, openclaw_token)
#   2. If none: generate and store /etc/ai-ops-runner/secrets/openclaw_admin_token (0640, 1000:1000)
#   3. Ensure OPENCLAW_ADMIN_TOKEN wired to console (deploy_pipeline already does this)
#   4. Restart console if needed so admin token is loaded
#   5. curl -X POST http://127.0.0.1:8787/api/exec -H "Content-Type: application/json" \
#        -H "X-OpenClaw-Token: $(cat secret)" -d '{"action":"deploy_and_verify"}'
#
# NO secrets printed. Output only fingerprints and redacted proof.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Resolve aiops-1 host
HOST="${AIOPS_HOST:-}"
USER="${AIOPS_USER:-root}"
if [ -z "$HOST" ]; then
  TARGETS="$HOME/.config/openclaw/targets.json"
  if [ -f "$TARGETS" ]; then
    HOST="$(python3 -c "
import json
try:
  d = json.load(open('$TARGETS'))
  t = d.get('targets', {}).get(d.get('active', ''), {})
  print(t.get('host', ''))
except: pass
" 2>/dev/null)"
  fi
fi
if [ -z "$HOST" ] || ! echo "$HOST" | grep -qE '^100\.[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "ERROR: AIOPS_HOST not set or not Tailscale CGNAT. Set AIOPS_HOST or configure ~/.config/openclaw/targets.json" >&2
  exit 2
fi

echo "=== bootstrap_admin_and_deploy.sh ==="
echo "  Target: $USER@$HOST"
echo ""

_ssh() {
  ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o BatchMode=yes "$USER@$HOST" "$@"
}

# Remote script: bootstrap admin + trigger deploy
REMOTE_SCRIPT='
set -euo pipefail
cd /opt/ai-ops-runner

# 1. Discover existing token
ADMIN_TOKEN=""
for f in /etc/ai-ops-runner/secrets/openclaw_admin_token \
         /etc/ai-ops-runner/secrets/openclaw_console_token \
         /etc/ai-ops-runner/secrets/openclaw_api_token \
         /etc/ai-ops-runner/secrets/openclaw_token; do
  if [ -f "$f" ]; then
    ADMIN_TOKEN="$(cat "$f" 2>/dev/null | tr -d "[:space:]")"
    [ -n "$ADMIN_TOKEN" ] && echo "ADMIN_SOURCE=$f" && break
  fi
done

# 2. If none, generate and store
if [ -z "$ADMIN_TOKEN" ]; then
  sudo mkdir -p /etc/ai-ops-runner/secrets
  ADMIN_TOKEN="$(od -A n -t x4 -N 16 /dev/urandom 2>/dev/null | tr -d " \n" || echo "$(date +%s)$$")"
  echo -n "$ADMIN_TOKEN" | sudo tee /etc/ai-ops-runner/secrets/openclaw_admin_token >/dev/null
  sudo chmod 0640 /etc/ai-ops-runner/secrets/openclaw_admin_token
  sudo chown 1000:1000 /etc/ai-ops-runner/secrets/openclaw_admin_token 2>/dev/null || true
  echo "ADMIN_SOURCE=generated"
fi

# Fingerprint only (first 8 chars)
FP="${ADMIN_TOKEN:0:8}..."
echo "ADMIN_FP=${FP}"

# 3+4. Rebuild console so OPENCLAW_ADMIN_TOKEN is loaded
CONSOLE_TOKEN=""
[ -f /etc/ai-ops-runner/secrets/openclaw_console_token ] && CONSOLE_TOKEN="$(cat /etc/ai-ops-runner/secrets/openclaw_console_token 2>/dev/null | tr -d "[:space:]")"
[ -z "$CONSOLE_TOKEN" ] && CONSOLE_TOKEN="$ADMIN_TOKEN"
export OPENCLAW_CONSOLE_TOKEN="$CONSOLE_TOKEN"
export OPENCLAW_ADMIN_TOKEN="$ADMIN_TOKEN"
export AIOPS_HOST="$(tailscale ip -4 2>/dev/null | head -n1 | tr -d "[:space:]")"
docker compose -f docker-compose.yml -f docker-compose.console.yml up -d --build 2>&1 | tail -5

# Wait for console to be ready
sleep 5
for i in 1 2 3 4 5 6 7 8 9 10; do
  curl -sf --connect-timeout 2 --max-time 5 http://127.0.0.1:8787/api/ai-status >/dev/null 2>&1 && break
  sleep 2
done

# 5. Trigger deploy_and_verify
RESP="$(curl -sf -X POST http://127.0.0.1:8787/api/exec \
  -H "Content-Type: application/json" \
  -H "X-OpenClaw-Token: $ADMIN_TOKEN" \
  -d "{\"action\":\"deploy_and_verify\"}" 2>/dev/null)" || true
echo "EXEC_RESPONSE=$RESP"
'

_ssh "bash -s" <<< "$REMOTE_SCRIPT"

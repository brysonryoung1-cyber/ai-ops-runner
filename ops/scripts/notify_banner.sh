#!/usr/bin/env bash
# notify_banner.sh — Minimal notification abstraction. Writes to HQ banner store.
#
# Usage:
#   ./ops/scripts/notify_banner.sh WAITING_FOR_HUMAN '{"novnc_url":"https://...","instruction":"Log in to Kajabi"}'
#   ./ops/scripts/notify_banner.sh CANARY_DEGRADED '{"failed_invariant":"novnc_audit","proof_paths":["artifacts/system/canary/..."]}'
#
# Writes to: artifacts/system/notification_banner.json (HQ reads via /api/notifications/banner)
# Optional: OPENCLAW_NOTIFY_WEBHOOK_URL → POST JSON; OPENCLAW_DISCORD_WEBHOOK → Discord
# No secrets printed.
set -euo pipefail

TYPE="${1:?Usage: notify_banner.sh TYPE JSON_PAYLOAD}"
JSON_PAYLOAD="${2:-{}}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ARTIFACTS="${OPENCLAW_ARTIFACTS_ROOT:-$ROOT_DIR/artifacts}"
BANNER_FILE="$ARTIFACTS/system/notification_banner.json"
mkdir -p "$(dirname "$BANNER_FILE")"

# Build banner payload
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TMP_JSON="$(mktemp)"
echo "$JSON_PAYLOAD" > "$TMP_JSON"
BANNER=$(python3 -c "
import json
payload = {}
try:
    with open('$TMP_JSON') as f:
        payload = json.load(f)
except: pass
banner = {'type': '$TYPE', 'created_at': '$TS', **payload}
print(json.dumps(banner, indent=2))
" 2>/dev/null)
rm -f "$TMP_JSON"
[ -z "$BANNER" ] && BANNER="{\"type\":\"$TYPE\",\"created_at\":\"$TS\"}"

echo "$BANNER" > "$BANNER_FILE"

# Optional webhook
WEBHOOK="${OPENCLAW_NOTIFY_WEBHOOK_URL:-}"
if [ -n "$WEBHOOK" ]; then
  curl -sf -X POST "$WEBHOOK" -H "Content-Type: application/json" -d "$BANNER" >/dev/null 2>&1 || true
fi

# Optional Discord
DISCORD="${OPENCLAW_DISCORD_WEBHOOK:-}"
if [ -n "$DISCORD" ]; then
  MSG="OpenClaw: $TYPE — $(echo "$BANNER" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('instruction', d.get('failed_invariant', d.get('message', ''))))" 2>/dev/null)"
  curl -sf -X POST "$DISCORD" -H "Content-Type: application/json" \
    -d "{\"content\":\"$MSG\"}" >/dev/null 2>&1 || true
fi

# Pushover via openclaw_notify (if configured)
if [ -f "$ROOT_DIR/ops/openclaw_notify.sh" ] && [ "$TYPE" = "WAITING_FOR_HUMAN" ]; then
  NOVNC=$(echo "$BANNER" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('novnc_url',''))" 2>/dev/null)
  INST=$(echo "$BANNER" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('instruction','Human action required'))" 2>/dev/null)
  "$ROOT_DIR/ops/openclaw_notify.sh" --priority high "WAITING_FOR_HUMAN: $INST ${NOVNC:0:50}..." 2>/dev/null || true
fi

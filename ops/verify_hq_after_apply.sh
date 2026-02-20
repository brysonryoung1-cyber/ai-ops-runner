#!/usr/bin/env bash
# verify_hq_after_apply.sh — After Apply OpenClaw (Remote) succeeds, verify health_public and autopilot/status.
#
# Usage:
#   BASE="https://aiops-1.tailc75c62.ts.net"  # or your HQ Tailscale URL
#   TOKEN="your-X-OpenClaw-Token"              # optional if no token auth
#   ./ops/verify_hq_after_apply.sh
#
# Or: OPENCLAW_HQ_BASE=https://... OPENCLAW_HQ_TOKEN=... ./ops/verify_hq_after_apply.sh
#
# Prints the deliverables block: build_sha, autopilot HTTP code + key fields.
set -euo pipefail

BASE="${OPENCLAW_HQ_BASE:-}"
TOKEN="${OPENCLAW_HQ_TOKEN:-}"

if [ -z "$BASE" ]; then
  echo "Usage: OPENCLAW_HQ_BASE=https://your-hq.tailnet.ts.net [OPENCLAW_HQ_TOKEN=...] $0" >&2
  echo "Or set BASE= and optionally TOKEN= in the script." >&2
  exit 1
fi

H_AUTH=""
[ -n "$TOKEN" ] && H_AUTH="-H X-OpenClaw-Token: $TOKEN"

echo "=== Verify HQ after Apply ==="
echo "  BASE = $BASE"
echo ""

# health_public
echo "==> GET /api/ui/health_public"
HP_CODE=""
HP_BODY=""
HP_CODE=$(curl -sS -o /tmp/hq_hp.json -w "%{http_code}" $H_AUTH "$BASE/api/ui/health_public" 2>/dev/null || echo "000")
if [ -f /tmp/hq_hp.json ]; then
  HP_BODY=$(cat /tmp/hq_hp.json)
else
  HP_BODY="{}"
fi
echo "  HTTP code: $HP_CODE"
BUILD_SHA="unknown"
if [ "$HP_CODE" = "200" ]; then
  BUILD_SHA=$(echo "$HP_BODY" | jq -r '.build_sha // "unknown"')
  echo "  build_sha: $BUILD_SHA"
else
  echo "  body: $HP_BODY"
fi
echo ""

# autopilot/status
echo "==> GET /api/autopilot/status"
AP_CODE=""
AP_BODY=""
AP_CODE=$(curl -sS -o /tmp/hq_ap.json -w "%{http_code}" $H_AUTH "$BASE/api/autopilot/status" 2>/dev/null || echo "000")
if [ -f /tmp/hq_ap.json ]; then
  AP_BODY=$(cat /tmp/hq_ap.json)
else
  AP_BODY="{}"
fi
echo "  HTTP code: $AP_CODE"
if [ "$AP_CODE" = "200" ]; then
  echo "  installed: $(echo "$AP_BODY" | jq -r '.installed // "null"')"
  echo "  enabled:   $(echo "$AP_BODY" | jq -r '.enabled // "null"')"
  echo "  last_deployed_sha: $(echo "$AP_BODY" | jq -r '.last_deployed_sha // "null"')"
else
  echo "  body: $AP_BODY"
fi
echo ""

echo "=== Deliverables (paste back) ==="
echo "- health_public HTTP: $HP_CODE | build_sha: $BUILD_SHA"
echo "- autopilot/status HTTP: $AP_CODE | installed: $(echo "$AP_BODY" | jq -r '.installed // "null"') | enabled: $(echo "$AP_BODY" | jq -r '.enabled // "null"')"
rm -f /tmp/hq_hp.json /tmp/hq_ap.json

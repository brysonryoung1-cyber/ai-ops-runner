#!/usr/bin/env bash
# green_check.sh — Assert production is green: /api/ai-status ok, /api/projects includes infra_openclaw+soma_kajabi, /api/dod/last exists + overall PASS
#
# Used by deploy_until_green.sh after deploy_pipeline. Exit 0 only if all checks pass.
# Binds to localhost/tailnet only. No secrets in output.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

BASE_URL="${OPENCLAW_VERIFY_BASE_URL:-http://127.0.0.1:8787}"
FAILURES=0

CURL_OPTS="-sf --connect-timeout 5 --max-time 15 --retry 2 --retry-delay 1"
check_ok() { echo "$1" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('ok') is True else 1)" 2>/dev/null; }
json_get() {
  local json="$1" path="$2"
  echo "$json" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for k in [p for p in \"$path\".strip('.').split('.') if p]:
  d=d.get(k) if isinstance(d,dict) else None
  if d is None: break
print(d if isinstance(d, (str, int, float, bool)) else (json.dumps(d) if d is not None else ''))
" 2>/dev/null || echo ""
}

# 1. /api/ai-status ok:true
AI_RESP="$(curl $CURL_OPTS "$BASE_URL/api/ai-status" 2>/dev/null)" || true
if [ -z "$AI_RESP" ] || ! check_ok "$AI_RESP"; then
  echo "FAIL: /api/ai-status unreachable or ok != true" >&2
  FAILURES=$((FAILURES + 1))
fi

# 2. /api/projects includes infra_openclaw and soma_kajabi
PROJ_RESP="$(curl $CURL_OPTS "$BASE_URL/api/projects" 2>/dev/null)" || true
if [ -z "$PROJ_RESP" ] || ! check_ok "$PROJ_RESP"; then
  echo "FAIL: /api/projects unreachable or ok != true" >&2
  FAILURES=$((FAILURES + 1))
else
  PROJ_IDS="$(echo "$PROJ_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
projects=d.get('projects',[]) if isinstance(d,dict) else []
ids=[p.get('id','') for p in projects if isinstance(p,dict)]
print(' '.join(ids))
" 2>/dev/null)" || true
  if ! echo "$PROJ_IDS" | grep -q "infra_openclaw" || ! echo "$PROJ_IDS" | grep -q "soma_kajabi"; then
    echo "FAIL: /api/projects missing infra_openclaw or soma_kajabi" >&2
    FAILURES=$((FAILURES + 1))
  fi
fi

# 3. /api/dod/last exists (HTTP 200) AND overall PASS
DOD_HTTP="$(curl -s -o /tmp/green_dod_last.$$ -w '%{http_code}' --connect-timeout 5 --max-time 10 "$BASE_URL/api/dod/last" 2>/dev/null)" || DOD_HTTP="000"
DOD_BODY="$(cat /tmp/green_dod_last.$$ 2>/dev/null)"
rm -f /tmp/green_dod_last.$$
if [ "$DOD_HTTP" != "200" ]; then
  echo "FAIL: /api/dod/last returned HTTP $DOD_HTTP (expect 200)" >&2
  FAILURES=$((FAILURES + 1))
elif [ -z "$DOD_BODY" ]; then
  echo "FAIL: /api/dod/last empty response" >&2
  FAILURES=$((FAILURES + 1))
else
  OVERALL="$(json_get "$DOD_BODY" "overall" 2>/dev/null || echo "")"
  if [ "$OVERALL" != "PASS" ]; then
    echo "FAIL: /api/dod/last overall=$OVERALL (expect PASS)" >&2
    FAILURES=$((FAILURES + 1))
  fi
fi

if [ "$FAILURES" -gt 0 ]; then
  exit 1
fi
echo "green_check: PASS"
exit 0

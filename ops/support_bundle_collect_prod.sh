#!/usr/bin/env bash
# support_bundle_collect_prod.sh — Collect diagnostics bundle from production
#
# Calls the same endpoints as the UI "Collect Diagnostics" button against the
# production base URL and writes a local artifact bundle. Deterministic, redacts
# tokens, runs without UI/browser.
#
# Required env:
#   OPENCLAW_VERIFY_BASE_URL — e.g. https://aiops-1.tailc75c62.ts.net
#
# Optional env:
#   OPENCLAW_ADMIN_TOKEN — admin token for authenticated endpoints
#   OPENCLAW_BUNDLE_DIR  — output directory (default: artifacts/support_bundle/<timestamp>)
#
# Usage:
#   OPENCLAW_VERIFY_BASE_URL=https://... ./ops/support_bundle_collect_prod.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

BASE_URL="${OPENCLAW_VERIFY_BASE_URL:?ERROR: Set OPENCLAW_VERIFY_BASE_URL}"
ADMIN_TOKEN="${OPENCLAW_ADMIN_TOKEN:-}"
STAMP="$(date -u +%Y%m%d_%H%M%S)-$(head -c 4 /dev/urandom | xxd -p)"
BUNDLE_DIR="${OPENCLAW_BUNDLE_DIR:-$ROOT_DIR/artifacts/support_bundle/$STAMP}"

mkdir -p "$BUNDLE_DIR"

echo "=== support_bundle_collect_prod.sh ==="
echo "  Base URL:   $BASE_URL"
echo "  Bundle dir: $BUNDLE_DIR"
echo ""

# Redact tokens from curl output
redact() {
  sed -E \
    -e 's/sk-[a-zA-Z0-9_-]{20,}/[REDACTED_OPENAI_KEY]/g' \
    -e 's/ghp_[a-zA-Z0-9]{36}/[REDACTED_GH_TOKEN]/g' \
    -e 's/Bearer [A-Za-z0-9._-]{20,}/Bearer [REDACTED]/g' \
    -e 's/AKIA[A-Z0-9]{16}/[REDACTED_AWS_KEY]/g'
}

curl_get() {
  local path="$1"
  local out_file="$2"
  local headers=(-H "Accept: application/json")
  if [ -n "$ADMIN_TOKEN" ]; then
    headers+=(-H "X-OpenClaw-Token: $ADMIN_TOKEN")
  fi
  local rc=0
  curl -fsSL --max-time 15 "${headers[@]}" "${BASE_URL}${path}" 2>/dev/null | redact > "$out_file" || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "{\"error\": \"curl failed (rc=$rc)\", \"path\": \"$path\"}" | redact > "$out_file"
  fi
}

# 1. Health public
echo "  [1/7] /api/ui/health_public"
curl_get "/api/ui/health_public" "$BUNDLE_DIR/health_public.json"

# 2. Auth status
echo "  [2/7] /api/auth/status"
curl_get "/api/auth/status" "$BUNDLE_DIR/auth_status.json"

# 3. Projects
echo "  [3/7] /api/projects"
curl_get "/api/projects" "$BUNDLE_DIR/projects.json"

# 4. DoD last
echo "  [4/7] /api/dod/last"
curl_get "/api/dod/last" "$BUNDLE_DIR/dod_last.json"

# 5. Recent runs
echo "  [5/7] /api/runs?limit=10"
curl_get "/api/runs?limit=10" "$BUNDLE_DIR/last_10_runs.json"

# 6. Host executor status
echo "  [6/7] /api/host-executor/status"
curl_get "/api/host-executor/status" "$BUNDLE_DIR/host_executor_status.json"

# 7. Server-side support bundle (triggers full collection)
echo "  [7/7] POST /api/support/bundle"
BUNDLE_HEADERS=(-H "Content-Type: application/json" -H "Accept: application/json")
if [ -n "$ADMIN_TOKEN" ]; then
  BUNDLE_HEADERS+=(-H "X-OpenClaw-Token: $ADMIN_TOKEN")
fi
BUNDLE_RC=0
BUNDLE_RESPONSE="$(curl -fsSL --max-time 30 -X POST "${BUNDLE_HEADERS[@]}" "${BASE_URL}/api/support/bundle" 2>/dev/null | redact)" || BUNDLE_RC=$?
echo "$BUNDLE_RESPONSE" > "$BUNDLE_DIR/bundle_response.json"

# Write manifest
python3 -c "
import json, os, sys
bundle_dir = sys.argv[1]
files = sorted(f for f in os.listdir(bundle_dir) if os.path.isfile(os.path.join(bundle_dir, f)))
manifest = {
    'collected_at': '$(date -u +%Y-%m-%dT%H:%M:%SZ)',
    'base_url': '${BASE_URL}',
    'files': files,
    'admin_token_provided': $([ -n "$ADMIN_TOKEN" ] && echo 'True' || echo 'False'),
}
with open(os.path.join(bundle_dir, 'MANIFEST.json'), 'w') as f:
    json.dump(manifest, f, indent=2)
" "$BUNDLE_DIR"

echo ""
echo "=== Bundle Complete ==="
echo "  artifact_dir: $BUNDLE_DIR"
echo "  run_id: $STAMP"

# Validate build_sha is not "unknown"
BUILD_SHA="$(python3 -c "
import json, sys
try:
    with open(sys.argv[1]) as f:
        d = json.load(f)
    print(d.get('build_sha', 'unknown'))
except: print('unknown')
" "$BUNDLE_DIR/health_public.json" 2>/dev/null || echo "unknown")"

if [ "$BUILD_SHA" = "unknown" ]; then
  echo "  WARNING: build_sha is 'unknown' — OPENCLAW_BUILD_SHA env may not be set"
else
  echo "  build_sha: $BUILD_SHA"
fi

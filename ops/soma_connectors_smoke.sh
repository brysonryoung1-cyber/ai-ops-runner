#!/usr/bin/env bash
# Production-mode smoke: hit POST /api/projects/soma_kajabi/run for bootstrap + status.
# Assert ok:true OR well-formed fail-closed (error_class + artifact_dir). Write proof to artifacts/ui_smoke_prod/<run_id>.
# Usage: BASE_URL=http://127.0.0.1:8787 ./ops/soma_connectors_smoke.sh
# Requires: OPENCLAW_REPO_ROOT or run from repo root.

set -e
BASE_URL="${BASE_URL:-http://127.0.0.1:8787}"
REPO_ROOT="${OPENCLAW_REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
ARTIFACT_DIR="$REPO_ROOT/artifacts/ui_smoke_prod/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$ARTIFACT_DIR"

echo "==> Soma Connectors production smoke (BASE_URL=$BASE_URL)"
echo "    Artifact dir: $ARTIFACT_DIR"

for action in soma_kajabi_bootstrap_start soma_connectors_status; do
  echo "==> POST /api/projects/soma_kajabi/run { action: $action }"
  resp="$(curl -s -w "\n%{http_code}" -X POST "$BASE_URL/api/projects/soma_kajabi/run" \
    -H "Content-Type: application/json" \
    -d "{\"action\":\"$action\"}")"
  http_code="$(echo "$resp" | tail -n1)"
  body="$(echo "$resp" | sed '$d')"
  echo "$body" | jq . 2>/dev/null || echo "$body"
  echo "$body" > "$ARTIFACT_DIR/${action}.json"
  echo "    HTTP $http_code" >> "$ARTIFACT_DIR/${action}.json.summary"

  ok="$(echo "$body" | jq -r '.ok // empty')"
  error_class="$(echo "$body" | jq -r '.error_class // empty')"
  artifact_dir="$(echo "$body" | jq -r '.artifact_dir // empty')"
  run_id="$(echo "$body" | jq -r '.run_id // empty')"

  if [ "$ok" = "true" ]; then
    echo "    PASS: ok:true, run_id=$run_id"
  elif [ -n "$error_class" ] && [ -n "$artifact_dir" ]; then
    echo "    PASS: fail-closed (error_class=$error_class, artifact_dir=$artifact_dir)"
  else
    echo "    FAIL: expected ok:true or error_class+artifact_dir"
    echo "fail" > "$ARTIFACT_DIR/result"
    exit 1
  fi
done

echo "pass" > "$ARTIFACT_DIR/result"
echo "==> Smoke PASS; proof in $ARTIFACT_DIR"

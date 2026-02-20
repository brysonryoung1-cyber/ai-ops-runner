#!/usr/bin/env bash
# hq_apply.sh — No-click Apply: trigger Apply via POST /api/exec, poll /api/runs until done, print PROOF BLOCK.
# Inputs: OPENCLAW_HQ_BASE (default http://127.0.0.1:8787), OPENCLAW_HQ_TOKEN (optional if trust_tailscale),
#         action name (default: apply).
# No secrets printed. Output: run_id, status, error_summary, artifact link.
set -euo pipefail

HQ_BASE="${OPENCLAW_HQ_BASE:-http://127.0.0.1:8787}"
HQ_TOKEN="${OPENCLAW_HQ_TOKEN:-}"
ACTION="${1:-apply}"
POLL_INTERVAL="${POLL_INTERVAL:-5}"
MAX_WAIT="${MAX_WAIT:-600}"

url_strip() {
  echo "$1" | sed 's|^https\?://||' | sed 's|/.*||'
}

trigger() {
  local curl_args=(-sS -X POST "${HQ_BASE}/api/exec" -H "Content-Type: application/json" -d "{\"action\":\"${ACTION}\"}")
  if [ -n "$HQ_TOKEN" ]; then
    curl_args+=(-H "X-OpenClaw-Token: $HQ_TOKEN")
  fi
  curl "${curl_args[@]}"
}

poll_run() {
  local run_id="$1"
  local curl_args=(-sS "${HQ_BASE}/api/runs?id=${run_id}")
  if [ -n "$HQ_TOKEN" ]; then
    curl_args+=(-H "X-OpenClaw-Token: $HQ_TOKEN")
  fi
  curl "${curl_args[@]}"
}

# Parse JSON value by key (simple; works for "key": "value" or "key": number)
json_get() {
  echo "$1" | sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\1/p" | head -1
}
json_get_num() {
  echo "$1" | sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\\([0-9]*\\).*/\1/p" | head -1
}

echo "Triggering action=${ACTION} at ${HQ_BASE}..."
resp="$(trigger)"
if ! echo "$resp" | grep -q '"run_id"'; then
  echo "Trigger failed or no run_id in response:"
  echo "$resp" | head -20
  exit 1
fi

run_id="$(json_get "$resp" "run_id")"
if [ -z "$run_id" ]; then
  echo "Could not parse run_id from response"
  echo "$resp"
  exit 1
fi

echo "run_id=$run_id — polling up to ${MAX_WAIT}s..."
elapsed=0
status=""
exit_code=""
error_summary=""
artifact_dir=""
while [ "$elapsed" -lt "$MAX_WAIT" ]; do
  json="$(poll_run "$run_id")"
  if echo "$json" | grep -q '"run"'; then
    # GET /api/runs?id=X returns { ok, run: { run_id, status, exit_code, error_summary, artifact_dir, ... } }
    status="$(json_get "$json" "status")"
    exit_code="$(json_get_num "$json" "exit_code")"
    error_summary="$(json_get "$json" "error_summary")"
    artifact_dir="$(json_get "$json" "artifact_dir")"
    if [ -n "$status" ] && [ "$status" != "null" ]; then
      break
    fi
  fi
  sleep "$POLL_INTERVAL"
  elapsed=$((elapsed + POLL_INTERVAL))
done

# PROOF BLOCK (no secrets)
echo "--- PROOF BLOCK ---"
echo "run_id: $run_id"
echo "status: ${status:-unknown}"
echo "exit_code: ${exit_code:-—}"
if [ -n "$error_summary" ] && [ "$error_summary" != "null" ]; then
  echo "error_summary: $error_summary"
fi
if [ -n "$artifact_dir" ] && [ "$artifact_dir" != "null" ]; then
  echo "artifact_dir: $artifact_dir"
  echo "artifacts_link: ${HQ_BASE}/artifacts/${artifact_dir#artifacts/}"
fi
echo "--- END PROOF BLOCK ---"

if [ "$status" = "success" ]; then
  exit 0
fi
exit 1

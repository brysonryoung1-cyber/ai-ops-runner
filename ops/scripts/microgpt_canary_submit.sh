#!/usr/bin/env bash
# Submit llm.microgpt.canary to test_runner API and wait for completion.
# Run from host (e.g. by OpenClaw action). Uses current repo at ROOT for job context.
set -euo pipefail
ROOT="${OPENCLAW_REPO_ROOT:-/opt/ai-ops-runner}"
cd "$ROOT"
API_BASE="${API_BASE:-http://127.0.0.1:8000}"
REPO_NAME="${REPO_NAME:-ai-ops-runner}"
REMOTE_URL="${REMOTE_URL:-$(git -C "$ROOT" remote get-url origin 2>/dev/null || echo "https://github.com/user/ai-ops-runner.git")}"
SHA="${SHA:-$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo "0000000000000000000000000000000000000000")}"
POLL_INTERVAL=5
POLL_MAX=60

response="$(curl -sf -X POST "$API_BASE/jobs" \
  -H "Content-Type: application/json" \
  -d "{\"job_type\":\"llm.microgpt.canary\",\"repo_name\":\"$REPO_NAME\",\"remote_url\":\"$REMOTE_URL\",\"sha\":\"$SHA\"}" 2>/dev/null)" || true
if [[ -z "$response" ]]; then
  echo "ERROR: Failed to submit llm.microgpt.canary (API at $API_BASE)"
  exit 1
fi
job_id="$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null)"
artifact_dir="$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('artifact_dir',''))" 2>/dev/null)"
if [[ -z "$job_id" ]]; then
  echo "ERROR: No job_id in response"
  exit 1
fi
echo "job_id=$job_id artifact_dir=$artifact_dir"

for _ in $(seq 1 "$POLL_MAX"); do
  status="$(curl -sf "$API_BASE/jobs/$job_id" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)" || true
  case "$status" in
    success) echo "llm.microgpt.canary PASS job_id=$job_id"; exit 0 ;;
    failure|error|timeout) echo "llm.microgpt.canary FAIL status=$status job_id=$job_id"; exit 1 ;;
  esac
  sleep "$POLL_INTERVAL"
done
echo "llm.microgpt.canary TIMEOUT waiting for job_id=$job_id"
exit 1

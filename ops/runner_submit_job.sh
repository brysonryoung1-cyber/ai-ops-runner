#!/usr/bin/env bash
# runner_submit_job.sh – Submit a job and wait for completion.
# Usage: ./ops/runner_submit_job.sh <job_type> <repo_name> <remote_url> <sha> [idempotency_key]
set -euo pipefail

API_BASE="${API_BASE:-http://localhost:8000}"
JOB_TYPE="${1:?Usage: $0 <job_type> <repo_name> <remote_url> <sha> [idempotency_key]}"
REPO_NAME="${2:?Missing repo_name}"
REMOTE_URL="${3:?Missing remote_url}"
SHA="${4:?Missing sha}"
IDEMPOTENCY_KEY="${5:-}"

echo "==> Submitting job: type=$JOB_TYPE repo=$REPO_NAME sha=$SHA"

BODY=$(cat <<EOF
{
  "job_type": "$JOB_TYPE",
  "repo_name": "$REPO_NAME",
  "remote_url": "$REMOTE_URL",
  "sha": "$SHA"
  $([ -n "$IDEMPOTENCY_KEY" ] && echo ", \"idempotency_key\": \"$IDEMPOTENCY_KEY\"")
}
EOF
)

RESPONSE=$(curl -s -X POST "$API_BASE/jobs" \
  -H "Content-Type: application/json" \
  -d "$BODY")

JOB_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
ARTIFACT_DIR=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['artifact_dir'])")

echo "==> Job submitted: id=$JOB_ID artifact_dir=$ARTIFACT_DIR"

# Poll until finished
while true; do
  STATUS_RESPONSE=$(curl -s "$API_BASE/jobs/$JOB_ID")
  STATUS=$(echo "$STATUS_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")

  case "$STATUS" in
    success|failure|error|timeout)
      echo "==> Job finished: status=$STATUS"
      echo "$STATUS_RESPONSE" | python3 -m json.tool
      break
      ;;
    *)
      echo "    status=$STATUS, waiting..."
      sleep 2
      ;;
  esac
done

echo ""
echo "==> Artifact dir: $ARTIFACT_DIR"

# Tail stdout
echo ""
echo "==> stdout (last 50 lines):"
LOGS=$(curl -s "$API_BASE/jobs/$JOB_ID/logs?stream=stdout&tail=50")
echo "$LOGS" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for line in data.get('lines', []):
    print(line, end='')
"

# Exit with job's exit code
EXIT_CODE=$(echo "$STATUS_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('exit_code', 1))")
exit "${EXIT_CODE:-0}"

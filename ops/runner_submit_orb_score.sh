#!/usr/bin/env bash
# runner_submit_orb_score.sh — Submit orb_score_run job, poll, print artifacts.
# Usage: ./ops/runner_submit_orb_score.sh [sha] [logs_day] [run_id]
#   sha defaults to remote HEAD; logs_day and run_id are optional.
set -euo pipefail

API_BASE="${API_BASE:-http://localhost:8000}"
ORB_REMOTE_URL="${ORB_REMOTE_URL:-https://github.com/brysonryoung1-cyber/algo-nt8-orb.git}"
ORB_SHA="${1:-HEAD}"
LOGS_DAY="${2:-}"
RUN_ID="${3:-}"

# Resolve HEAD if needed
if [ "$ORB_SHA" = "HEAD" ]; then
  echo "==> Resolving HEAD for $ORB_REMOTE_URL..."
  ORB_SHA=$(git ls-remote "$ORB_REMOTE_URL" HEAD | cut -f1)
  if [ -z "$ORB_SHA" ]; then
    echo "ERROR: Could not resolve HEAD" >&2
    exit 1
  fi
  echo "    resolved: $ORB_SHA"
fi

echo "==> Submitting orb_score_run job"
echo "    remote_url=$ORB_REMOTE_URL"
echo "    sha=$ORB_SHA"
echo "    logs_day=${LOGS_DAY:-<none>}"
echo "    run_id=${RUN_ID:-<none>}"

# Build params
PARAMS_JSON="{}"
if [ -n "$LOGS_DAY" ] || [ -n "$RUN_ID" ]; then
  PARAMS_JSON="{"
  SEP=""
  if [ -n "$LOGS_DAY" ]; then
    PARAMS_JSON="${PARAMS_JSON}\"logs_day\": \"$LOGS_DAY\""
    SEP=", "
  fi
  if [ -n "$RUN_ID" ]; then
    PARAMS_JSON="${PARAMS_JSON}${SEP}\"run_id\": \"$RUN_ID\""
  fi
  PARAMS_JSON="${PARAMS_JSON}}"
fi

RESPONSE=$(curl -sf -X POST "$API_BASE/jobs" \
  -H "Content-Type: application/json" \
  -d "{
    \"job_type\": \"orb_score_run\",
    \"repo_name\": \"algo-nt8-orb\",
    \"remote_url\": \"$ORB_REMOTE_URL\",
    \"sha\": \"$ORB_SHA\",
    \"params\": $PARAMS_JSON
  }")

JOB_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "    job_id=$JOB_ID"
echo "    artifact_dir=./artifacts/$JOB_ID"

# Poll
echo ""
echo "==> Waiting for job to finish..."
while true; do
  STATUS_RESPONSE=$(curl -s "$API_BASE/jobs/$JOB_ID")
  STATUS=$(echo "$STATUS_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  case "$STATUS" in
    success|failure|error|timeout)
      echo "==> Job finished: status=$STATUS"
      break
      ;;
    *)
      echo "    status=$STATUS ..."
      sleep 3
      ;;
  esac
done

echo ""
echo "==> Job details:"
echo "$STATUS_RESPONSE" | python3 -m json.tool

# Show score output
echo ""
echo "==> Artifact dir: ./artifacts/$JOB_ID"
if [ -f "./artifacts/$JOB_ID/SCORE_OUTPUT.txt" ]; then
  echo ""
  echo "==> SCORE_OUTPUT.txt:"
  cat "./artifacts/$JOB_ID/SCORE_OUTPUT.txt"
else
  echo "    (SCORE_OUTPUT.txt not found in artifacts)"
fi

# Show invariants
if [ -f "./artifacts/$JOB_ID/artifact.json" ]; then
  echo ""
  echo "==> Invariants:"
  python3 -c "
import json
with open('./artifacts/$JOB_ID/artifact.json') as f:
    d = json.load(f)
inv = d.get('invariants', {})
print(f'  read_only_ok:  {inv.get(\"read_only_ok\", \"N/A\")}')
print(f'  clean_tree_ok: {inv.get(\"clean_tree_ok\", \"N/A\")}')
"
fi

EXIT_CODE=$(echo "$STATUS_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('exit_code', 1))")
exit "${EXIT_CODE:-0}"

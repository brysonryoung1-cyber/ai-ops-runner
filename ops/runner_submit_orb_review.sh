#!/usr/bin/env bash
# runner_submit_orb_review.sh — Submit orb_review_bundle job, poll, print artifacts.
# Usage: ./ops/runner_submit_orb_review.sh [sha] [since_sha]
#   sha defaults to remote HEAD; since_sha is optional.
set -euo pipefail

API_BASE="${API_BASE:-http://localhost:8000}"
ORB_REMOTE_URL="${ORB_REMOTE_URL:-git@github.com:brysonryoung1-cyber/algo-nt8-orb.git}"
ORB_SHA="${1:-HEAD}"
SINCE_SHA="${2:-}"

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

echo "==> Submitting orb_review_bundle job"
echo "    remote_url=$ORB_REMOTE_URL"
echo "    sha=$ORB_SHA"
echo "    since_sha=${SINCE_SHA:-<auto>}"

# Build params
PARAMS=""
if [ -n "$SINCE_SHA" ]; then
  PARAMS=", \"params\": {\"since_sha\": \"$SINCE_SHA\"}"
fi

RESPONSE=$(curl -sf -X POST "$API_BASE/jobs" \
  -H "Content-Type: application/json" \
  -d "{
    \"job_type\": \"orb_review_bundle\",
    \"repo_name\": \"algo-nt8-orb\",
    \"remote_url\": \"$ORB_REMOTE_URL\",
    \"sha\": \"$ORB_SHA\"
    $PARAMS
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

# Show artifact preview
echo ""
echo "==> Artifact dir: ./artifacts/$JOB_ID"
if [ -f "./artifacts/$JOB_ID/REVIEW_BUNDLE.txt" ]; then
  echo ""
  echo "==> REVIEW_BUNDLE.txt preview (first 50 lines):"
  head -50 "./artifacts/$JOB_ID/REVIEW_BUNDLE.txt"
else
  echo "    (REVIEW_BUNDLE.txt not found in artifacts)"
fi

# Show invariants from artifact.json
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

# Exit with job's exit code
EXIT_CODE=$(echo "$STATUS_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('exit_code', 1))")
exit "${EXIT_CODE:-0}"

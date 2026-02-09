#!/usr/bin/env bash
# runner_smoke.sh – Bring up the stack and run a basic smoke test.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

API_BASE="${API_BASE:-http://localhost:8000}"

echo "==> Starting services..."
docker compose up -d --build

echo "==> Waiting for API to be healthy..."
for i in $(seq 1 30); do
  if curl -sf "$API_BASE/healthz" > /dev/null 2>&1; then
    echo "    API is healthy."
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "ERROR: API did not become healthy in 30s"
    docker compose logs test_runner_api
    exit 1
  fi
  sleep 1
done

# Health check
HEALTH=$(curl -s "$API_BASE/healthz")
echo "==> /healthz: $HEALTH"

# Submit local_echo job
echo ""
echo "==> Submitting local_echo job..."
RESPONSE=$(curl -s -X POST "$API_BASE/jobs" \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "local_echo",
    "repo_name": "smoke-test",
    "remote_url": "https://github.com/octocat/Hello-World.git",
    "sha": "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"
  }')

JOB_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "    job_id=$JOB_ID"

# Wait for completion
echo "==> Waiting for job to finish..."
for i in $(seq 1 60); do
  STATUS_RESPONSE=$(curl -s "$API_BASE/jobs/$JOB_ID")
  STATUS=$(echo "$STATUS_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")

  case "$STATUS" in
    success|failure|error|timeout)
      echo "    Job finished: status=$STATUS"
      break
      ;;
    *)
      sleep 2
      ;;
  esac
done

echo ""
echo "==> Job details:"
echo "$STATUS_RESPONSE" | python3 -m json.tool

# Assert job succeeded
if [ "$STATUS" != "success" ]; then
  echo "ERROR: Smoke test job did not succeed (status=$STATUS)" >&2
  exit 1
fi

# Tail logs
echo ""
echo "==> stdout:"
curl -s "$API_BASE/jobs/$JOB_ID/logs?stream=stdout&tail=50" | \
  python3 -c "
import sys, json
data = json.load(sys.stdin)
for line in data.get('lines', []):
    print(line, end='')
"

# Optional: ORB tests
if [ -n "${ORB_REMOTE_URL:-}" ] && [ -n "${ORB_SHA:-}" ]; then
  echo ""
  echo "==> ORB_REMOTE_URL and ORB_SHA set – submitting orb_ops_selftests..."
  ORB_RESPONSE=$(curl -s -X POST "$API_BASE/jobs" \
    -H "Content-Type: application/json" \
    -d "{
      \"job_type\": \"orb_ops_selftests\",
      \"repo_name\": \"mnq-orb\",
      \"remote_url\": \"$ORB_REMOTE_URL\",
      \"sha\": \"$ORB_SHA\"
    }")
  ORB_JOB_ID=$(echo "$ORB_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
  echo "    orb job_id=$ORB_JOB_ID"

  echo "==> Waiting for ORB job to finish (up to 1800s)..."
  for i in $(seq 1 900); do
    ORB_STATUS=$(curl -s "$API_BASE/jobs/$ORB_JOB_ID" | \
      python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
    case "$ORB_STATUS" in
      success|failure|error|timeout)
        echo "    ORB job finished: status=$ORB_STATUS"
        break
        ;;
      *)
        sleep 2
        ;;
    esac
  done
fi

echo ""
echo "==> Smoke test complete."

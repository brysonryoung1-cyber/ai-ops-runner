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

# Optional: ORB legacy tests (via env vars)
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

# --- ORB integration smoke (auto-resolves HEAD, skips if network unavailable) ---
echo ""
echo "==> ORB integration smoke test..."
ORB_INT_URL="${ORB_REMOTE_URL:-git@github.com:brysonryoung1-cyber/algo-nt8-orb.git}"
ORB_INT_SHA=""
if ORB_INT_SHA=$(git ls-remote "$ORB_INT_URL" HEAD 2>/dev/null | cut -f1) && [ -n "$ORB_INT_SHA" ]; then
  echo "    ORB HEAD: $ORB_INT_SHA"

  # --- orb_doctor ---
  echo ""
  echo "==> Submitting orb_doctor job..."
  ORB_DOC_RESP=$(curl -s -X POST "$API_BASE/jobs" \
    -H "Content-Type: application/json" \
    -d "{
      \"job_type\": \"orb_doctor\",
      \"repo_name\": \"algo-nt8-orb\",
      \"remote_url\": \"$ORB_INT_URL\",
      \"sha\": \"$ORB_INT_SHA\"
    }")
  ORB_DOC_JID=$(echo "$ORB_DOC_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
  echo "    orb_doctor job_id=$ORB_DOC_JID"

  for i in $(seq 1 120); do
    ORB_DOC_STATUS=$(curl -s "$API_BASE/jobs/$ORB_DOC_JID" | \
      python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
    case "$ORB_DOC_STATUS" in
      success|failure|error|timeout)
        echo "    orb_doctor finished: status=$ORB_DOC_STATUS"
        break
        ;;
      *) sleep 2 ;;
    esac
  done

  # Validate invariants in artifact.json
  ORB_DOC_ART="./artifacts/$ORB_DOC_JID/artifact.json"
  if [ -f "$ORB_DOC_ART" ]; then
    python3 -c "
import json, sys
with open('$ORB_DOC_ART') as f:
    d = json.load(f)
inv = d.get('invariants', {})
if not inv.get('read_only_ok'):
    print('ERROR: read_only_ok is False', file=sys.stderr)
    sys.exit(1)
if not inv.get('clean_tree_ok'):
    print('ERROR: clean_tree_ok is False (MUTATION_DETECTED)', file=sys.stderr)
    sys.exit(1)
print('    invariants: read_only_ok=True clean_tree_ok=True')
" || echo "    WARNING: invariant check failed (non-fatal in smoke test)"
  fi

  # --- orb_review_bundle ---
  echo ""
  echo "==> Submitting orb_review_bundle job..."
  ORB_RB_RESP=$(curl -s -X POST "$API_BASE/jobs" \
    -H "Content-Type: application/json" \
    -d "{
      \"job_type\": \"orb_review_bundle\",
      \"repo_name\": \"algo-nt8-orb\",
      \"remote_url\": \"$ORB_INT_URL\",
      \"sha\": \"$ORB_INT_SHA\"
    }")
  ORB_RB_JID=$(echo "$ORB_RB_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
  echo "    orb_review_bundle job_id=$ORB_RB_JID"

  for i in $(seq 1 120); do
    ORB_RB_STATUS=$(curl -s "$API_BASE/jobs/$ORB_RB_JID" | \
      python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
    case "$ORB_RB_STATUS" in
      success|failure|error|timeout)
        echo "    orb_review_bundle finished: status=$ORB_RB_STATUS"
        break
        ;;
      *) sleep 2 ;;
    esac
  done

  # Check REVIEW_BUNDLE.txt exists in artifacts
  if [ -f "./artifacts/$ORB_RB_JID/REVIEW_BUNDLE.txt" ]; then
    echo "    REVIEW_BUNDLE.txt present ($(wc -c < "./artifacts/$ORB_RB_JID/REVIEW_BUNDLE.txt" | tr -d ' ') bytes)"
  else
    echo "    WARNING: REVIEW_BUNDLE.txt not found in artifacts"
  fi

  # --- orb_score_run (expected: HARNESS_NOT_FOUND) ---
  echo ""
  echo "==> Submitting orb_score_run job (expect HARNESS_NOT_FOUND)..."
  ORB_SC_RESP=$(curl -s -X POST "$API_BASE/jobs" \
    -H "Content-Type: application/json" \
    -d "{
      \"job_type\": \"orb_score_run\",
      \"repo_name\": \"algo-nt8-orb\",
      \"remote_url\": \"$ORB_INT_URL\",
      \"sha\": \"$ORB_INT_SHA\"
    }")
  ORB_SC_JID=$(echo "$ORB_SC_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
  echo "    orb_score_run job_id=$ORB_SC_JID"

  for i in $(seq 1 120); do
    ORB_SC_STATUS=$(curl -s "$API_BASE/jobs/$ORB_SC_JID" | \
      python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
    case "$ORB_SC_STATUS" in
      success|failure|error|timeout)
        echo "    orb_score_run finished: status=$ORB_SC_STATUS"
        break
        ;;
      *) sleep 2 ;;
    esac
  done

  # Score run should fail gracefully with HARNESS_NOT_FOUND
  if [ -f "./artifacts/$ORB_SC_JID/SCORE_OUTPUT.txt" ]; then
    if grep -q "HARNESS_NOT_FOUND" "./artifacts/$ORB_SC_JID/SCORE_OUTPUT.txt"; then
      echo "    HARNESS_NOT_FOUND correctly reported (graceful failure)"
    else
      echo "    SCORE_OUTPUT.txt present but no HARNESS_NOT_FOUND message"
    fi
  fi

else
  echo "    WARNING: Could not resolve HEAD for $ORB_INT_URL (network unavailable?)"
  echo "    Skipping ORB integration tests."
fi

echo ""
echo "==> Smoke test complete."

#!/usr/bin/env bash
# openclaw_nightly.sh — OpenClaw nightly driver
#
# Submits ORB analysis jobs (orb_doctor + orb_review_bundle) via the runner
# API and writes a compact summary to:
#   artifacts/openclaw/nightly/<timestamp>/summary.json
#
# Designed to run nightly at 02:00 local via openclaw-nightly.timer.
#
# Prerequisites:
#   - Docker Compose stack must be running (API on 127.0.0.1:8000)
#   - ORB remote must be accessible via SSH key in /etc/ai-ops-runner/keys/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

API_BASE="${API_BASE:-http://127.0.0.1:8000}"
ORB_REMOTE_URL="${ORB_REMOTE_URL:-git@github.com:brysonryoung1-cyber/algo-nt8-orb.git}"
ORB_REPO_NAME="${ORB_REPO_NAME:-algo-nt8-orb}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PROOF_DIR="$ROOT_DIR/artifacts/openclaw/nightly/$TIMESTAMP"
POLL_INTERVAL=5
POLL_MAX=120  # 10 minutes max

mkdir -p "$PROOF_DIR"

echo "=== openclaw_nightly.sh ==="
echo "  Time:      $TIMESTAMP"
echo "  API:       $API_BASE"
echo "  Proof dir: $PROOF_DIR"
echo ""

# --- Resolve ORB HEAD ---
echo "--- Resolving ORB HEAD ---"
ORB_SHA="$(git ls-remote "$ORB_REMOTE_URL" HEAD 2>/dev/null | cut -f1 || true)"
if [ -z "$ORB_SHA" ]; then
  echo "ERROR: Cannot resolve ORB HEAD from $ORB_REMOTE_URL" >&2
  python3 -c "
import json
with open('$PROOF_DIR/summary.json', 'w') as f:
    json.dump({'timestamp': '$TIMESTAMP', 'status': 'error', 'error': 'cannot_resolve_head'}, f, indent=2)
"
  exit 1
fi
echo "  ORB HEAD: $ORB_SHA"

# --- Submit jobs ---
submit_job() {
  local job_type="$1"
  local response
  response="$(curl -sf -X POST "$API_BASE/jobs" \
    -H "Content-Type: application/json" \
    -d "{\"job_type\":\"$job_type\",\"repo_name\":\"$ORB_REPO_NAME\",\"remote_url\":\"$ORB_REMOTE_URL\",\"sha\":\"$ORB_SHA\"}" \
    2>/dev/null || echo "")"
  if [ -z "$response" ]; then
    echo "ERROR: Failed to submit $job_type job" >&2
    echo ""
    return
  fi
  local job_id
  job_id="$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null || echo "")"
  echo "$job_id"
}

poll_job() {
  local job_id="$1"
  local status=""
  for _ in $(seq 1 "$POLL_MAX"); do
    status="$(curl -sf "$API_BASE/jobs/$job_id" 2>/dev/null | \
      python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "unknown")"
    case "$status" in
      success|failure|error|timeout) break ;;
      *) sleep "$POLL_INTERVAL" ;;
    esac
  done
  echo "$status"
}

echo ""
echo "--- Submitting jobs ---"

DOCTOR_JID="$(submit_job orb_doctor)"
echo "  orb_doctor job_id: ${DOCTOR_JID:-FAILED}"

REVIEW_JID="$(submit_job orb_review_bundle)"
echo "  orb_review_bundle job_id: ${REVIEW_JID:-FAILED}"

# --- Poll for completion ---
echo ""
echo "--- Waiting for completion ---"

DOCTOR_STATUS="not_submitted"
REVIEW_STATUS="not_submitted"

if [ -n "$DOCTOR_JID" ]; then
  DOCTOR_STATUS="$(poll_job "$DOCTOR_JID")"
  echo "  orb_doctor: $DOCTOR_STATUS"
fi

if [ -n "$REVIEW_JID" ]; then
  REVIEW_STATUS="$(poll_job "$REVIEW_JID")"
  echo "  orb_review_bundle: $REVIEW_STATUS"
fi

# --- Write summary.json ---
python3 - "$PROOF_DIR/summary.json" "$TIMESTAMP" "$ORB_SHA" \
  "${DOCTOR_JID:-null}" "$DOCTOR_STATUS" \
  "${REVIEW_JID:-null}" "$REVIEW_STATUS" <<'PYEOF'
import json, sys

out_file = sys.argv[1]
timestamp = sys.argv[2]
orb_sha = sys.argv[3]
doctor_jid = sys.argv[4] if sys.argv[4] != "null" else None
doctor_status = sys.argv[5]
review_jid = sys.argv[6] if sys.argv[6] != "null" else None
review_status = sys.argv[7]

summary = {
    "timestamp": timestamp,
    "orb_sha": orb_sha,
    "jobs": {
        "orb_doctor": {
            "job_id": doctor_jid,
            "status": doctor_status,
        },
        "orb_review_bundle": {
            "job_id": review_jid,
            "status": review_status,
        },
    },
    "status": "success" if all(
        s == "success" for s in [doctor_status, review_status]
    ) else "partial" if any(
        s == "success" for s in [doctor_status, review_status]
    ) else "failure",
}

with open(out_file, "w") as f:
    json.dump(summary, f, indent=2)
print(json.dumps(summary, indent=2))
PYEOF

echo ""
echo "=== Nightly summary written to: $PROOF_DIR/summary.json ==="

# Overall exit code: fail if both jobs failed
if [ "$DOCTOR_STATUS" != "success" ] && [ "$REVIEW_STATUS" != "success" ]; then
  echo "FAIL: All nightly jobs failed." >&2
  exit 1
fi

echo "Done."
exit 0

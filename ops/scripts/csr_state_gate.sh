#!/usr/bin/env bash
# csr_state_gate.sh — Deterministic green short-circuit.
#
# Exit codes:
#   0 = GREEN and recent (< threshold) → safe to skip work
#   1 = NOT green or stale
#   2 = no gate (no RESULT.json found)
#
# Usage: csr_state_gate.sh [threshold_minutes]
#   threshold_minutes defaults to CSR_STATE_GATE_THRESHOLD_MIN (env) or 15.
set -euo pipefail

THRESHOLD_MIN="${1:-${CSR_STATE_GATE_THRESHOLD_MIN:-15}}"
ARTIFACTS_ROOT="${CSR_ARTIFACTS_ROOT:-/opt/ai-ops-runner/artifacts}"
POST_DEPLOY_DIR="$ARTIFACTS_ROOT/post_deploy"

if [ ! -d "$POST_DEPLOY_DIR" ]; then
  echo "NO_GATE: post_deploy dir missing"
  exit 2
fi

LATEST_RESULT=""
LATEST_MTIME=0

for f in "$POST_DEPLOY_DIR"/*/RESULT.json; do
  [ -f "$f" ] || continue
  if [[ "$OSTYPE" == darwin* ]]; then
    mt=$(stat -f '%m' "$f" 2>/dev/null) || continue
  else
    mt=$(stat -c '%Y' "$f" 2>/dev/null) || continue
  fi
  if [ "$mt" -gt "$LATEST_MTIME" ]; then
    LATEST_MTIME=$mt
    LATEST_RESULT="$f"
  fi
done

if [ -z "$LATEST_RESULT" ]; then
  echo "NO_GATE: no RESULT.json found"
  exit 2
fi

GATE_JSON=$(python3 -c "
import json, sys, time
with open('$LATEST_RESULT') as f:
    d = json.load(f)
overall = d.get('overall', '')
ts = d.get('timestamp', '')
age_sec = time.time() - $LATEST_MTIME
threshold = $THRESHOLD_MIN * 60
print(json.dumps({'overall': overall, 'age_sec': int(age_sec), 'threshold_sec': threshold, 'path': '$LATEST_RESULT', 'timestamp': ts}))
" 2>/dev/null) || { echo "NO_GATE: parse error"; exit 2; }

OVERALL=$(echo "$GATE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['overall'])")
AGE_SEC=$(echo "$GATE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['age_sec'])")
THRESHOLD_SEC=$(echo "$GATE_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['threshold_sec'])")

if [ "$OVERALL" = "PASS" ] && [ "$AGE_SEC" -lt "$THRESHOLD_SEC" ]; then
  echo "GREEN ${AGE_SEC}s ago (threshold=${THRESHOLD_SEC}s) $LATEST_RESULT"
  exit 0
fi

echo "NOT_GREEN overall=$OVERALL age=${AGE_SEC}s threshold=${THRESHOLD_SEC}s"
exit 1

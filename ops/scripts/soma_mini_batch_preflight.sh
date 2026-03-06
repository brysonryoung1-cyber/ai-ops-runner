#!/usr/bin/env bash
# soma_mini_batch_preflight.sh — 3-attempt Soma mini-batch using preflight.
# Runs on aiops-1. Validates soma_preflight + notifications loop.
#
# Usage: ./ops/scripts/soma_mini_batch_preflight.sh
# Or via SSH: ssh root@aiops-1 'cd /opt/ai-ops-runner && ./ops/scripts/soma_mini_batch_preflight.sh'
#
# Deliverable: artifacts/system/soma_mini_batch_preflight/<UTC_TS>/
set -euo pipefail

REPO_DIR="${1:-/opt/ai-ops-runner}"
cd "$REPO_DIR"
export PYTHONPATH="${REPO_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

UTC_TS="$(date -u +%Y%m%dT%H%M%SZ)"
ART_ROOT="${OPENCLAW_ARTIFACTS_ROOT:-$REPO_DIR/artifacts}"
ART_DIR="$ART_ROOT/system/soma_mini_batch_preflight/$UTC_TS"
mkdir -p "$ART_DIR"

ATTEMPTS_FILE="$ART_DIR/attempts.jsonl"
: > "$ATTEMPTS_FILE"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$ART_DIR/batch.log" >&2; }

# Notification tracking (from project_autopilot RESULT.json alert field)
NOTIFY_HASHES=()
NOTIFY_DEDUPED=0
NOTIFY_COUNT=0

for attempt in 1 2 3; do
  log "=== Attempt $attempt/3 ==="
  PREFLIGHT_RUN_ID="soma_preflight_batch_${UTC_TS}_a${attempt}"
  RUN_ID="project_autopilot_batch_${UTC_TS}_a${attempt}"

  # Run project_autopilot (preflight + soma_run_to_done when GO)
  BUNDLE_DIR="$ART_ROOT/system/project_autopilot/$RUN_ID"
  mkdir -p "$BUNDLE_DIR"

  log "Running project_autopilot (preflight + soma_run_to_done)..."
  AP_OUT="$ART_DIR/attempt_${attempt}_stdout.log"
  AP_ERR="$ART_DIR/attempt_${attempt}_stderr.log"
  set +e
  python3 ops/system/project_autopilot.py \
    --project soma_kajabi \
    --action soma_run_to_done \
    --max-seconds 2100 \
    --poll-interval 6..24 \
    --hq-base "http://127.0.0.1:8787" \
    > "$AP_OUT" 2> "$AP_ERR"
  AP_RC=$?
  set -e

  # Parse RESULT.json from most recent project_autopilot run (we don't control run_id)
  RESULT_JSON=$(ls -t "$ART_ROOT/system/project_autopilot"/project_autopilot_*/RESULT.json 2>/dev/null | head -1)
  [ -z "$RESULT_JSON" ] && RESULT_JSON="$BUNDLE_DIR/RESULT.json"
  STATUS="UNKNOWN"
  ERROR_CLASS=""
  REASONS=()
  NOVNC_URL=""
  GATE_EXPIRY=""
  ALERT_DEDUPED=""
  ALERT_HASH=""
  PREFLIGHT_STATUS=""
  RUN_ARTIFACT_DIR=""

  if [ -f "$RESULT_JSON" ]; then
    STATUS=$(python3 -c "import json; d=json.load(open('$RESULT_JSON')); print(d.get('status','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
    ERROR_CLASS=$(python3 -c "import json; d=json.load(open('$RESULT_JSON')); print(d.get('error_class',''))" 2>/dev/null || echo "")
    PREFLIGHT_STATUS=$(python3 -c "import json; d=json.load(open('$RESULT_JSON')); p=d.get('preflight',{}); print(p.get('status',''))" 2>/dev/null || echo "")
    NOVNC_URL=$(python3 -c "import json; d=json.load(open('$RESULT_JSON')); print(d.get('novnc_url','') or '')" 2>/dev/null || echo "")
    GATE_EXPIRY=$(python3 -c "import json; d=json.load(open('$RESULT_JSON')); print(d.get('gate_expiry','') or '')" 2>/dev/null || echo "")
    ALERT=$(python3 -c "import json; d=json.load(open('$RESULT_JSON')); a=d.get('alert',{}); print(json.dumps(a) if a else '{}')" 2>/dev/null || echo "{}")
    ALERT_DEDUPED=$(echo "$ALERT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('deduped', False))" 2>/dev/null || echo "false")
    ALERT_HASH=$(echo "$ALERT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('hash',''))" 2>/dev/null || echo "")
    REASONS_JSON=$(python3 -c "import json; d=json.load(open('$RESULT_JSON')); p=d.get('preflight',{}); print(json.dumps(p.get('reasons',[])))" 2>/dev/null || echo "[]")
    RUN_ARTIFACT_DIR=$(python3 -c "import json; d=json.load(open('$RESULT_JSON')); print(d.get('run_artifact_dir','') or d.get('remote_run_id',''))" 2>/dev/null || echo "")
  fi

  if [ -n "$ALERT_HASH" ]; then
    NOTIFY_HASHES+=("$ALERT_HASH")
    NOTIFY_COUNT=$((NOTIFY_COUNT + 1))
    [ "$ALERT_DEDUPED" = "True" ] && NOTIFY_DEDUPED=$((NOTIFY_DEDUPED + 1))
  fi

  REASONS_ESC="${REASONS_JSON:-[]}"
  RESULT_REL=$(python3 -c "import os; print(os.path.relpath('$RESULT_JSON','$REPO_DIR'))" 2>/dev/null || echo "")
  DEDUPED_BOOL="False"
  [ "$ALERT_DEDUPED" = "True" ] && DEDUPED_BOOL="True"
  ATTEMPT_RECORD=$(python3 -c "
import json
rec = {
  'attempt': $attempt,
  'timestamp_utc': '$(date -u +%Y-%m-%dT%H:%M:%SZ)',
  'status': '$STATUS',
  'error_class': '$ERROR_CLASS',
  'preflight_status': '$PREFLIGHT_STATUS',
  'reasons': $REASONS_ESC,
  'novnc_url': '$NOVNC_URL' if '$NOVNC_URL' else None,
  'gate_expiry': '$GATE_EXPIRY' if '$GATE_EXPIRY' else None,
  'alert_deduped': $DEDUPED_BOOL,
  'alert_hash': '$ALERT_HASH' if '$ALERT_HASH' else None,
  'result_path': '$RESULT_REL',
  'run_artifact_dir': '$RUN_ARTIFACT_DIR',
  'exit_code': $AP_RC,
}
print(json.dumps(rec))
")
  echo "$ATTEMPT_RECORD" >> "$ATTEMPTS_FILE"

  log "Attempt $attempt: status=$STATUS preflight=$PREFLIGHT_STATUS error_class=$ERROR_CLASS"

  if [ "$STATUS" = "WAITING_FOR_HUMAN" ] || [ "$PREFLIGHT_STATUS" = "HUMAN_ONLY" ]; then
    log "HUMAN_ONLY: STOP. noVNC=$NOVNC_URL expiry=$GATE_EXPIRY dedupe=$ALERT_DEDUPED"
    break
  fi

  if [ "$PREFLIGHT_STATUS" = "NO_GO" ]; then
    log "NO_GO: STOP. reasons=$REASONS_JSON"
    break
  fi

  if [ $attempt -lt 3 ]; then
    JITTER=$((120 + RANDOM % 121))
    log "Sleeping ${JITTER}s before next attempt..."
    sleep "$JITTER"
  fi
done

# Outcomes histogram
python3 -c "
import json
from pathlib import Path
from collections import Counter
attempts = []
for line in Path('$ATTEMPTS_FILE').read_text().strip().splitlines():
    if line:
        attempts.append(json.loads(line))
statuses = [a.get('status','UNKNOWN') for a in attempts]
preflight_statuses = [a.get('preflight_status','') for a in attempts]
hist = {
    'status': dict(Counter(statuses)),
    'preflight_status': dict(Counter(s for s in preflight_statuses if s)),
    'total_attempts': len(attempts),
    'attempts': attempts,
}
Path('$ART_DIR/outcomes_histogram.json').write_text(json.dumps(hist, indent=2))
"

# Notifications observed
python3 -c "
import json
from pathlib import Path
attempts = []
for line in Path('$ATTEMPTS_FILE').read_text().strip().splitlines():
    if line:
        attempts.append(json.loads(line))
hashes = [a.get('alert_hash') for a in attempts if a.get('alert_hash')]
deduped_count = sum(1 for a in attempts if a.get('alert_deduped') is True)
payload = {
    'dedupe_hash': hashes[0] if hashes else None,
    'dedupe_hashes_seen': list(dict.fromkeys(hashes)),
    'count': len(hashes),
    'deduped_fired_count': $NOTIFY_DEDUPED,
}
Path('$ART_DIR/notifications_observed.json').write_text(json.dumps(payload, indent=2))
"

# Links to key artifacts
KEY_LINKS=(
  "artifacts/system/soma_mini_batch_preflight/$UTC_TS/attempts.jsonl"
  "artifacts/system/soma_mini_batch_preflight/$UTC_TS/outcomes_histogram.json"
  "artifacts/system/soma_mini_batch_preflight/$UTC_TS/notifications_observed.json"
  "artifacts/system/soma_mini_batch_preflight/$UTC_TS/links_to_key_artifacts.json"
  "artifacts/system/soma_mini_batch_preflight/$UTC_TS/PROOF_SUMMARY.md"
  "artifacts/system/soma_mini_batch_preflight/$UTC_TS/DECISION.md"
)
LATEST_RESULT=$(ls -t "$ART_ROOT/system/project_autopilot"/project_autopilot_*/RESULT.json 2>/dev/null | head -1)
if [ -n "$LATEST_RESULT" ] && [ -f "$LATEST_RESULT" ]; then
  REL=$(python3 -c "import os; print(os.path.relpath('$LATEST_RESULT', '$REPO_DIR'))")
  KEY_LINKS+=("$REL")
fi
python3 -c "
import json
from pathlib import Path
links = $(printf '%s\n' "${KEY_LINKS[@]}" | python3 -c "import json,sys; print(json.dumps([x.strip() for x in sys.stdin if x.strip()]))")
Path('$ART_DIR/links_to_key_artifacts.json').write_text(json.dumps({'artifacts': links}, indent=2))
"

# PROOF_SUMMARY.md
{
  echo "# Soma Mini-Batch Preflight Proof"
  echo ""
  echo "**Timestamp:** $UTC_TS"
  echo "**Artifact dir:** $ART_DIR"
  echo ""
  echo "## Attempts"
  cat "$ATTEMPTS_FILE" | while read -r line; do
    [ -n "$line" ] && echo "- $line"
  done
  echo ""
  echo "## Outcomes"
  cat "$ART_DIR/outcomes_histogram.json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get('status',{}), indent=2))" 2>/dev/null || true
  echo ""
  echo "## Notifications"
  cat "$ART_DIR/notifications_observed.json" 2>/dev/null || true
} > "$ART_DIR/PROOF_SUMMARY.md"

# DECISION.md
LAST_STATUS=$(tail -1 "$ATTEMPTS_FILE" 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('status','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
LAST_PREFLIGHT=$(tail -1 "$ATTEMPTS_FILE" 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('preflight_status',''))" 2>/dev/null || echo "")
DECISION="no"
REASON=""
if [ "$LAST_STATUS" = "SUCCESS" ]; then
  DECISION="yes"
  REASON="At least one run reached SUCCESS; Soma operational."
elif [ "$LAST_STATUS" = "WAITING_FOR_HUMAN" ] || [ "$LAST_PREFLIGHT" = "HUMAN_ONLY" ]; then
  DECISION="yes"
  REASON="HUMAN_ONLY observed; preflight correctly gated and stopped. No run thrash."
elif [ "$LAST_STATUS" = "NO_GO" ] || [ "$LAST_PREFLIGHT" = "NO_GO" ]; then
  REASON="NO_GO from preflight; system correctly blocked run. Investigate reasons before proceeding."
else
  REASON="Terminal status $LAST_STATUS; check error_class and artifacts."
fi

{
  echo "# DECISION"
  echo ""
  echo "**Is Soma operational enough to move on?** $DECISION"
  echo ""
  echo "**Why:** $REASON"
  echo ""
  echo "**Artifact path:** $ART_DIR"
} > "$ART_DIR/DECISION.md"

log "Batch complete. Artifacts: $ART_DIR"
echo "$ART_DIR"

#!/usr/bin/env bash
set -euo pipefail

HOST="aiops-1"
BASE_URL="https://aiops-1.tailc75c62.ts.net"
REPO_DIR="/opt/ai-ops-runner"

POLL_INTERVAL_SEC="${POLL_INTERVAL_SEC:-5}"
INITIAL_HEALTH_TIMEOUT_SEC="${INITIAL_HEALTH_TIMEOUT_SEC:-180}"
POST_REMEDIATION_TIMEOUT_SEC="${POST_REMEDIATION_TIMEOUT_SEC:-180}"
GRACE_502_SEC="${GRACE_502_SEC:-60}"

usage() {
  cat <<'USAGE'
Usage: ./ops/remote/aiops_apply_and_prove.sh [--host HOST] [--base-url URL] [--repo-dir DIR]

Defaults:
  --host aiops-1
  --base-url https://aiops-1.tailc75c62.ts.net
  --repo-dir /opt/ai-ops-runner

Writes local proof bundle to:
  artifacts/local_apply_proofs/<utc_run_id>/
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host)
      HOST="${2:-}"; shift 2 ;;
    --base-url)
      BASE_URL="${2:-}"; shift 2 ;;
    --repo-dir)
      REPO_DIR="${2:-}"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
export PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

RAND_SUFFIX="$(LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 6 || true)"
[ -z "$RAND_SUFFIX" ] && RAND_SUFFIX="000000"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_${RAND_SUFFIX}"
PROOF_DIR="$ROOT_DIR/artifacts/local_apply_proofs/$RUN_ID"
mkdir -p "$PROOF_DIR"

REMOTE_LOG="$PROOF_DIR/remote_actions.log"
COMPOSE_PS="$PROOF_DIR/compose_ps.txt"
COMPOSE_LOGS="$PROOF_DIR/compose_logs_tail.txt"
HEALTH_BEFORE="$PROOF_DIR/health_public_before.json"
HEALTH_AFTER="$PROOF_DIR/health_public_after.json"
RESULT_JSON="$PROOF_DIR/RESULT.json"
HEALTH_URL="${BASE_URL%/}/api/ui/health_public"

: > "$REMOTE_LOG"
: > "$COMPOSE_PS"
: > "$COMPOSE_LOGS"

STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

log() {
  local msg="$*"
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$msg" | tee -a "$REMOTE_LOG" >&2
}

SSH_OPTS=(
  -o ConnectTimeout=15
  -o StrictHostKeyChecking=accept-new
  -o BatchMode=yes
)

run_remote_script() {
  local label="$1"
  local script="$2"
  log "$label"
  if ! ssh "${SSH_OPTS[@]}" "$HOST" "bash -s" >>"$REMOTE_LOG" 2>&1 <<<"$script"; then
    log "FAILED: $label"
    return 1
  fi
  return 0
}

capture_health_snapshot() {
  local out_file="$1"
  local body_file="$PROOF_DIR/.health_body.tmp"
  local http_code

  http_code="$(curl -sS --connect-timeout 5 --max-time 15 -o "$body_file" -w "%{http_code}" "$HEALTH_URL" || true)"

  python3 - "$http_code" "$body_file" "$out_file" <<'PY'
import sys
from pathlib import Path

from ops.lib.aiops_remote_helpers import assess_health_public, utc_now_iso, write_json_file

raw_http = (sys.argv[1] or "").strip()
try:
    http_code = int(raw_http)
except ValueError:
    http_code = 0
body = ""
body_path = Path(sys.argv[2])
if body_path.exists():
    body = body_path.read_text(encoding="utf-8", errors="replace")
snapshot = assess_health_public(http_code, body)
snapshot["checked_at"] = utc_now_iso()
write_json_file(Path(sys.argv[3]), snapshot)
print(snapshot.get("state", "ERROR"))
PY
}

collect_remediation_evidence() {
  log "Collecting remote evidence: docker compose ps"
  ssh "${SSH_OPTS[@]}" "$HOST" "set -euo pipefail; cd '$REPO_DIR'; docker compose ps" >"$COMPOSE_PS" 2>>"$REMOTE_LOG" || true

  log "Collecting remote evidence: docker compose logs --tail=300"
  ssh "${SSH_OPTS[@]}" "$HOST" "set -euo pipefail; cd '$REPO_DIR'; docker compose logs --tail=300" >"$COMPOSE_LOGS" 2>>"$REMOTE_LOG" || true
}

restart_guarded_services() {
  run_remote_script "Remediation: guarded systemctl restart for frontdoor/hostd" "
set -euo pipefail
restarted=0
for unit in openclaw-frontdoor.service frontdoor.service openclaw-hostd.service hostd.service; do
  if systemctl cat \"\$unit\" >/dev/null 2>&1; then
    restarted=1
    echo \"restarting \$unit\"
    systemctl restart \"\$unit\"
  fi
done
if [ \"\$restarted\" -eq 0 ]; then
  echo 'no matching systemd units found; skipping guarded restart'
fi
"
}

deploy_ok=0
remediation_attempted=0
remediation_failed=0
health_ok=0

log "Run ID: $RUN_ID"
log "Proof bundle: $PROOF_DIR"
log "Checking health_public before deploy: $HEALTH_URL"
before_state="$(capture_health_snapshot "$HEALTH_BEFORE")"
log "health_public_before state=$before_state"

if run_remote_script "Deploy: git fetch + git reset --hard origin/main + docker compose up -d --build" "
set -euo pipefail
cd '$REPO_DIR'
git fetch origin
git reset --hard origin/main
docker compose up -d --build
"; then
  deploy_ok=1
fi

# Pointer files are not managed by this script.

poll_started_epoch="$(date +%s)"
deadline_epoch=$((poll_started_epoch + INITIAL_HEALTH_TIMEOUT_SEC))

while true; do
  now_epoch="$(date +%s)"
  after_state="$(capture_health_snapshot "$HEALTH_AFTER")"
  if [ "$after_state" = "OK" ]; then
    health_ok=1
    log "health_public_after state=OK"
    break
  fi

  elapsed_sec=$((now_epoch - poll_started_epoch))
  log "health_public_after state=$after_state elapsed=${elapsed_sec}s"

  if [ "$remediation_attempted" -eq 0 ] && [ "$elapsed_sec" -ge "$GRACE_502_SEC" ]; then
    remediation_attempted=1
    log "Health still not OK after grace window (${GRACE_502_SEC}s). Collecting evidence + remediation."
    collect_remediation_evidence
    if ! run_remote_script "Remediation: docker compose up -d" "
set -euo pipefail
cd '$REPO_DIR'
docker compose up -d
"; then
      remediation_failed=1
    fi
    if ! restart_guarded_services; then
      remediation_failed=1
    fi
    now_epoch="$(date +%s)"
    deadline_epoch=$((now_epoch + POST_REMEDIATION_TIMEOUT_SEC))
    log "Post-remediation health deadline extended by ${POST_REMEDIATION_TIMEOUT_SEC}s."
  fi

  if [ "$now_epoch" -ge "$deadline_epoch" ]; then
    log "Health polling timeout reached."
    break
  fi
  sleep "$POLL_INTERVAL_SEC"
done

after_state="$(capture_health_snapshot "$HEALTH_AFTER")"
[ "$after_state" = "OK" ] && health_ok=1

if [ "$remediation_attempted" -eq 0 ]; then
  # Guarantee required files exist even when no remediation was needed.
  : > "$COMPOSE_PS"
  : > "$COMPOSE_LOGS"
fi

finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

python3 - "$PROOF_DIR" "$RUN_ID" "$STARTED_AT" "$finished_at" "$HOST" "$BASE_URL" "$REPO_DIR" "$deploy_ok" "$remediation_attempted" "$remediation_failed" <<'PY'
import json
import sys
from pathlib import Path

from ops.lib.aiops_remote_helpers import build_apply_result, write_json_file

proof_dir = Path(sys.argv[1])
run_id = sys.argv[2]
started_at = sys.argv[3]
finished_at = sys.argv[4]
host = sys.argv[5]
base_url = sys.argv[6]
repo_dir = sys.argv[7]
deploy_ok = sys.argv[8] == "1"
remediation_attempted = sys.argv[9] == "1"
remediation_failed = sys.argv[10] == "1"

health_before = json.loads((proof_dir / "health_public_before.json").read_text(encoding="utf-8"))
health_after = json.loads((proof_dir / "health_public_after.json").read_text(encoding="utf-8"))

result = build_apply_result(
    run_id=run_id,
    started_at=started_at,
    finished_at=finished_at,
    host=host,
    base_url=base_url,
    repo_dir=repo_dir,
    health_before=health_before,
    health_after=health_after,
    deploy_ok=deploy_ok,
    remediation_attempted=remediation_attempted,
)
result["remediation_failed"] = remediation_failed
if remediation_failed and result["status"] == "PASS":
    result["status"] = "FAIL"
    result["summary"] = result["summary"] + ", remediation_failed=true"
write_json_file(proof_dir / "RESULT.json", result)
print(result["status"])
PY

result_status="$(python3 - "$RESULT_JSON" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(data.get("status", "FAIL"))
PY
)"

log "RESULT status=$result_status proof_dir=$PROOF_DIR"
if [ "$result_status" = "PASS" ]; then
  echo "PASS: apply_and_prove completed. Proof: $PROOF_DIR"
  exit 0
fi
echo "FAIL: apply_and_prove did not converge. Proof: $PROOF_DIR"
exit 1

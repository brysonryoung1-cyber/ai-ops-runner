#!/usr/bin/env bash
set -euo pipefail

HOST="aiops-1"
BASE_URL="https://aiops-1.tailc75c62.ts.net"
REPO_DIR="/opt/ai-ops-runner"

POLL_INTERVAL_SEC="${POLL_INTERVAL_SEC:-5}"
POLL_TIMEOUT_SEC="${POLL_TIMEOUT_SEC:-2100}"

usage() {
  cat <<'USAGE'
Usage: ./ops/remote/aiops_soma_run_to_done.sh [--host HOST] [--base-url URL] [--repo-dir DIR]

Defaults:
  --host aiops-1
  --base-url https://aiops-1.tailc75c62.ts.net
  --repo-dir /opt/ai-ops-runner

Exit codes:
  0 = SUCCESS
  1 = FAIL
  2 = WAITING_FOR_HUMAN (prints pinned noVNC URL)
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
LOCAL_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_soma_${RAND_SUFFIX}"
PROOF_DIR="$ROOT_DIR/artifacts/local_apply_proofs/$LOCAL_RUN_ID"
mkdir -p "$PROOF_DIR"

LOG_FILE="$PROOF_DIR/soma_remote.log"
HEALTH_FILE="$PROOF_DIR/health_public.json"
TRIGGER_FILE="$PROOF_DIR/trigger_response.json"
RUN_FILE="$PROOF_DIR/run_poll.json"
PROOF_BROWSE_FILE="$PROOF_DIR/proof_browse.json"
PROOF_PAYLOAD_FILE="$PROOF_DIR/proof_payload.json"
RESULT_FILE="$PROOF_DIR/soma_run_to_done_result.json"
REMOTE_OUTPUT_FILE="$PROOF_DIR/soma_remote_output.log"

: > "$LOG_FILE"

log() {
  local msg="$*"
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$msg" | tee -a "$LOG_FILE" >&2
}

SSH_OPTS=(
  -o ConnectTimeout=15
  -o StrictHostKeyChecking=accept-new
  -o BatchMode=yes
)

health_http_code="$(curl -sS --connect-timeout 5 --max-time 15 -o "$HEALTH_FILE" -w "%{http_code}" "${BASE_URL%/}/api/ui/health_public" || true)"
health_state="$(python3 - "$health_http_code" "$HEALTH_FILE" <<'PY'
import sys
from pathlib import Path

from ops.lib.aiops_remote_helpers import assess_health_public

raw_http = (sys.argv[1] or "").strip()
try:
    code = int(raw_http)
except ValueError:
    code = 0
body = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace")
print(assess_health_public(code, body).get("state", "ERROR"))
PY
)"

terminal_status="FAIL"
novnc_url=""
remote_run_id=""
mode_used=""

if [ "$health_state" = "OK" ]; then
  mode_used="hq_api"
  log "HQ UI reachable. Triggering soma_run_to_done via /api/exec."
  trigger_http_code="$(curl -sS --connect-timeout 5 --max-time 30 -X POST "${BASE_URL%/}/api/exec" -H "Content-Type: application/json" -d '{"action":"soma_run_to_done"}' -o "$TRIGGER_FILE" -w "%{http_code}" || true)"

  mapfile -t trigger_fields < <(python3 - "$trigger_http_code" "$TRIGGER_FILE" <<'PY'
import sys
from pathlib import Path

from ops.lib.aiops_remote_helpers import parse_exec_trigger_response

raw_http = (sys.argv[1] or "").strip()
try:
    code = int(raw_http)
except ValueError:
    code = 0
body = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace")
parsed = parse_exec_trigger_response(code, body)
print(parsed.get("state", "FAILED"))
print(parsed.get("run_id") or "")
print(parsed.get("message") or "")
PY
)
  trigger_state="${trigger_fields[0]:-FAILED}"
  remote_run_id="${trigger_fields[1]:-}"
  trigger_message="${trigger_fields[2]:-}"

  if [ "$trigger_state" = "FAILED" ] || [ -z "$remote_run_id" ]; then
    log "Trigger via HQ failed ($trigger_state). Falling back to remote SSH."
    mode_used="remote_ssh"
  else
    log "Polling /api/runs?id=$remote_run_id"
    deadline_epoch=$(( $(date +%s) + POLL_TIMEOUT_SEC ))
    while true; do
      now_epoch="$(date +%s)"
      if [ "$now_epoch" -ge "$deadline_epoch" ]; then
        terminal_status="FAIL"
        log "Polling timeout for run_id=$remote_run_id"
        break
      fi

      run_http_code="$(curl -sS --connect-timeout 5 --max-time 20 -o "$RUN_FILE" -w "%{http_code}" "${BASE_URL%/}/api/runs?id=${remote_run_id}" || true)"
      if [ "$run_http_code" != "200" ]; then
        sleep "$POLL_INTERVAL_SEC"
        continue
      fi

      mapfile -t run_fields < <(python3 - "$RUN_FILE" <<'PY'
import sys
from pathlib import Path

from ops.lib.aiops_remote_helpers import parse_run_poll_response

body = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
parsed = parse_run_poll_response(body)
print(parsed.get("status", ""))
print(parsed.get("artifact_dir") or "")
PY
)
      run_status="${run_fields[0]:-}"
      artifact_dir="${run_fields[1]:-}"

      if [ -n "$artifact_dir" ]; then
        proof_rel_path="${artifact_dir#artifacts/}/PROOF.json"
        proof_query_path="$(python3 - "$proof_rel_path" <<'PY'
import sys
from urllib.parse import quote

print(quote(sys.argv[1], safe=""))
PY
)"
        proof_http_code="$(curl -sS --connect-timeout 5 --max-time 20 -o "$PROOF_BROWSE_FILE" -w "%{http_code}" "${BASE_URL%/}/api/artifacts/browse?path=${proof_query_path}" || true)"
        if [ "$proof_http_code" = "200" ]; then
          python3 - "$PROOF_BROWSE_FILE" "$PROOF_PAYLOAD_FILE" <<'PY'
import json
import sys
from pathlib import Path

from ops.lib.aiops_remote_helpers import parse_artifact_browse_proof, write_json_file

body = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
proof = parse_artifact_browse_proof(body) or {}
write_json_file(Path(sys.argv[2]), proof)
print(json.dumps(proof))
PY
        fi
      fi

      if [ ! -f "$PROOF_PAYLOAD_FILE" ]; then
        : > "$PROOF_PAYLOAD_FILE"
      fi

      mapfile -t terminal_fields < <(python3 - "$run_status" "$PROOF_PAYLOAD_FILE" "$BASE_URL" <<'PY'
import json
import sys
from pathlib import Path

from ops.lib.aiops_remote_helpers import (
    canonical_novnc_url,
    classify_soma_terminal_status,
)

proof_path = Path(sys.argv[2])
proof_payload = {}
if proof_path.exists() and proof_path.read_text(encoding="utf-8", errors="replace").strip():
    try:
        proof_payload = json.loads(proof_path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        proof_payload = {}
parsed = classify_soma_terminal_status(sys.argv[1], proof_payload)
terminal = parsed.get("terminal_status", "RUNNING")
url = parsed.get("novnc_url") or ""
if terminal == "WAITING_FOR_HUMAN" and not url:
    url = canonical_novnc_url(sys.argv[3])
print(terminal)
print(url)
PY
)
      terminal_status="${terminal_fields[0]:-RUNNING}"
      novnc_url="${terminal_fields[1]:-}"

      if [ "$terminal_status" != "RUNNING" ]; then
        log "Terminal status from HQ poll: $terminal_status"
        break
      fi
      sleep "$POLL_INTERVAL_SEC"
    done
  fi
else
  mode_used="remote_ssh"
  trigger_message="UI health state=$health_state"
fi

if [ "$mode_used" = "remote_ssh" ]; then
  log "Using SSH fallback: python3 ops/scripts/soma_run_to_done.py"
  ssh_rc=0
  ssh "${SSH_OPTS[@]}" "$HOST" "set -euo pipefail; cd '$REPO_DIR'; python3 ops/scripts/soma_run_to_done.py" >"$REMOTE_OUTPUT_FILE" 2>>"$LOG_FILE" || ssh_rc=$?

  mapfile -t ssh_fields < <(python3 - "$REMOTE_OUTPUT_FILE" "$BASE_URL" "$ssh_rc" <<'PY'
import json
import sys
from pathlib import Path

from ops.lib.aiops_remote_helpers import (
    canonical_novnc_url,
    classify_soma_terminal_status,
    extract_last_json_object,
)

output = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
exit_code = int(sys.argv[3])
parsed = extract_last_json_object(output) or {}
proof_status = parsed.get("status")
run_status = "success" if exit_code == 0 else "failure"
proof_payload = {"status": proof_status, "novnc_url": parsed.get("novnc_url")} if proof_status else {}
state = classify_soma_terminal_status(run_status, proof_payload)
terminal = state.get("terminal_status", "FAIL")
if proof_status and str(proof_status).upper() not in ("SUCCESS", "WAITING_FOR_HUMAN"):
    terminal = "FAIL"
if not parsed and exit_code == 0:
    terminal = "FAIL"
novnc = state.get("novnc_url") or ""
if terminal == "WAITING_FOR_HUMAN" and not novnc:
    novnc = canonical_novnc_url(sys.argv[2])
print(terminal)
print(novnc)
print(json.dumps(parsed))
PY
)
  terminal_status="${ssh_fields[0]:-FAIL}"
  novnc_url="${ssh_fields[1]:-}"
  remote_payload_json="${ssh_fields[2]:-\{\}}"
  python3 - "$RESULT_FILE" "$LOCAL_RUN_ID" "$mode_used" "$terminal_status" "$novnc_url" "$remote_run_id" "$remote_payload_json" "$trigger_message" <<'PY'
import json
import sys
from pathlib import Path

from ops.lib.aiops_remote_helpers import utc_now_iso, write_json_file

payload = {
    "local_run_id": sys.argv[2],
    "mode": sys.argv[3],
    "terminal_status": sys.argv[4],
    "novnc_url": sys.argv[5] or None,
    "remote_run_id": sys.argv[6] or None,
    "remote_payload": json.loads(sys.argv[7]),
    "summary": sys.argv[8] or None,
    "finished_at": utc_now_iso(),
}
write_json_file(Path(sys.argv[1]), payload)
PY
else
  python3 - "$RESULT_FILE" "$LOCAL_RUN_ID" "$mode_used" "$terminal_status" "$novnc_url" "$remote_run_id" "$trigger_message" <<'PY'
import sys
from pathlib import Path

from ops.lib.aiops_remote_helpers import utc_now_iso, write_json_file

payload = {
    "local_run_id": sys.argv[2],
    "mode": sys.argv[3],
    "terminal_status": sys.argv[4],
    "novnc_url": sys.argv[5] or None,
    "remote_run_id": sys.argv[6] or None,
    "summary": sys.argv[7] or None,
    "finished_at": utc_now_iso(),
}
write_json_file(Path(sys.argv[1]), payload)
PY
fi

if [ "$terminal_status" = "SUCCESS" ]; then
  echo "SUCCESS: soma_run_to_done completed. Proof: $RESULT_FILE"
  exit 0
fi
if [ "$terminal_status" = "WAITING_FOR_HUMAN" ]; then
  if [ -z "$novnc_url" ]; then
    novnc_url="$(python3 - "$BASE_URL" <<'PY'
import sys
from ops.lib.aiops_remote_helpers import canonical_novnc_url

print(canonical_novnc_url(sys.argv[1]))
PY
)"
  fi
  echo "WAITING_FOR_HUMAN: $novnc_url"
  echo "Proof: $RESULT_FILE"
  exit 2
fi

echo "FAIL: soma_run_to_done did not complete successfully. Proof: $RESULT_FILE"
exit 1

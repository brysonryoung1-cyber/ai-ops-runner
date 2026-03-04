#!/usr/bin/env bash
set -euo pipefail

HOST="aiops-1"
BASE_URL="https://aiops-1.tailc75c62.ts.net"
REPO_DIR="/opt/ai-ops-runner"
HQ_LOCAL_PORT="${OPENCLAW_CONSOLE_PORT:-8787}"
HQ_LOCAL_BASE_URL="${OPENCLAW_HQ_LOCAL_BASE_URL:-http://127.0.0.1:${HQ_LOCAL_PORT}}"
CURL_BIN="${AIOPS_CURL_BIN:-curl}"
SSH_BIN="${AIOPS_SSH_BIN:-ssh}"

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
PRECHECK_BROWSE_FILE="$PROOF_DIR/precheck_browse.json"
PROOF_PAYLOAD_FILE="$PROOF_DIR/proof_payload.json"
PRECHECK_PAYLOAD_FILE="$PROOF_DIR/precheck_payload.json"
RESULT_FILE="$PROOF_DIR/soma_run_to_done_result.json"
STATUS_FILE="$PROOF_DIR/project_status.json"
REMOTE_OUTPUT_FILE="$PROOF_DIR/soma_remote_output.log"

: > "$LOG_FILE"

log() {
  local msg="$*"
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$msg" | tee -a "$LOG_FILE" >&2
}

# Bash 3.2 (macOS) compatibility: mapfile is bash 4+; we use read-loop. Log version; require bash >= 3 only.
log "bash_version=${BASH_VERSION:-unknown}"

SSH_OPTS=(
  -o ConnectTimeout=15
  -o StrictHostKeyChecking=accept-new
  -o BatchMode=yes
)

urlencode_path() {
  python3 - "$1" <<'PY'
import sys
from urllib.parse import quote

print(quote(sys.argv[1], safe=""))
PY
}

BROWSE_LAST_HTTP_CODE=""
BROWSE_LAST_HTTP_CODE_LOCAL=""
BROWSE_LAST_HTTP_CODE_REMOTE=""
BROWSE_LAST_MODE="local"
BROWSE_LAST_URL=""
BROWSE_LAST_REMOTE_URL=""

browse_dir_entries() {
  local browse_path="$1"
  local browse_mode="$2"
  local out_file="$3"
  local encoded_path local_url local_http remote_http remote_url remote_raw ssh_rc

  if [ -z "$out_file" ]; then
    BROWSE_LAST_HTTP_CODE="000"
    BROWSE_LAST_HTTP_CODE_LOCAL=""
    BROWSE_LAST_HTTP_CODE_REMOTE=""
    BROWSE_LAST_MODE=""
    BROWSE_LAST_URL=""
    BROWSE_LAST_REMOTE_URL=""
    return 1
  fi

  encoded_path="$(urlencode_path "$browse_path")"
  local_url="${BASE_URL%/}/api/artifacts/browse?path=${encoded_path}"

  BROWSE_LAST_HTTP_CODE=""
  BROWSE_LAST_HTTP_CODE_LOCAL=""
  BROWSE_LAST_HTTP_CODE_REMOTE=""
  BROWSE_LAST_MODE="local"
  BROWSE_LAST_URL="$local_url"
  BROWSE_LAST_REMOTE_URL=""
  : > "$out_file"

  if [ -n "${AIOPS_BROWSE_MOCK_HTTP_CODE:-}" ]; then
    local_http="${AIOPS_BROWSE_MOCK_HTTP_CODE}"
    if [ -n "${AIOPS_BROWSE_MOCK_BODY_FILE:-}" ] && [ -f "${AIOPS_BROWSE_MOCK_BODY_FILE}" ]; then
      cat "${AIOPS_BROWSE_MOCK_BODY_FILE}" >"$out_file"
    elif [ -n "${AIOPS_BROWSE_MOCK_BODY_JSON:-}" ]; then
      printf '%s' "${AIOPS_BROWSE_MOCK_BODY_JSON}" >"$out_file"
    fi
  else
    local_http="$("$CURL_BIN" -sS --connect-timeout 5 --max-time 20 -o "$out_file" -w "%{http_code}" "$local_url" || true)"
  fi
  [ -z "$local_http" ] && local_http="000"
  BROWSE_LAST_HTTP_CODE_LOCAL="$local_http"
  BROWSE_LAST_HTTP_CODE="$local_http"
  if [ "$local_http" = "200" ]; then
    return 0
  fi

  if [ "$browse_mode" = "local" ] || [ "${AIOPS_BROWSE_SKIP_REMOTE_FALLBACK:-0}" = "1" ]; then
    return 1
  fi

  BROWSE_LAST_MODE="remote_fallback"
  remote_url="${HQ_LOCAL_BASE_URL%/}/api/artifacts/browse?path=${encoded_path}"
  BROWSE_LAST_REMOTE_URL="$remote_url"

  if [ -n "${AIOPS_BROWSE_REMOTE_MOCK_HTTP_CODE:-}" ]; then
    remote_http="${AIOPS_BROWSE_REMOTE_MOCK_HTTP_CODE}"
    if [ -n "${AIOPS_BROWSE_REMOTE_MOCK_BODY_FILE:-}" ] && [ -f "${AIOPS_BROWSE_REMOTE_MOCK_BODY_FILE}" ]; then
      cat "${AIOPS_BROWSE_REMOTE_MOCK_BODY_FILE}" >"$out_file"
    elif [ -n "${AIOPS_BROWSE_REMOTE_MOCK_BODY_JSON:-}" ]; then
      printf '%s' "${AIOPS_BROWSE_REMOTE_MOCK_BODY_JSON}" >"$out_file"
    else
      : > "$out_file"
    fi
  else
    remote_raw="$PROOF_DIR/.browse_remote_${RANDOM}_${RANDOM}.txt"
    ssh_rc=0
    "$SSH_BIN" "${SSH_OPTS[@]}" "$HOST" "set -euo pipefail; tmp=\$(mktemp); code=\$(curl -sS --connect-timeout 5 --max-time 20 -o \"\$tmp\" -w \"%{http_code}\" '$remote_url' || true); printf '%s\n' \"\$code\"; cat \"\$tmp\"; rm -f \"\$tmp\"" >"$remote_raw" 2>>"$LOG_FILE" || ssh_rc=$?
    if [ "$ssh_rc" -eq 0 ] && [ -s "$remote_raw" ]; then
      remote_http="$(head -n 1 "$remote_raw" | tr -d '\r')"
      tail -n +2 "$remote_raw" >"$out_file"
    else
      remote_http="000"
      : > "$out_file"
    fi
    rm -f "$remote_raw"
  fi

  [ -z "$remote_http" ] && remote_http="000"
  BROWSE_LAST_HTTP_CODE_REMOTE="$remote_http"
  BROWSE_LAST_HTTP_CODE="$remote_http"
  [ "$remote_http" = "200" ]
}

RUN_DIR_BROWSE_HTTP_CODE=""
RUN_DIR_BROWSE_HTTP_CODE_LOCAL=""
RUN_DIR_BROWSE_HTTP_CODE_REMOTE=""
RUN_DIR_BROWSE_MODE=""
RUN_DIR_BROWSE_URL=""
RUN_DIR_BROWSE_REMOTE_URL=""
PROOF_HTTP_CODE=""
PROOF_HTTP_CODE_LOCAL=""
PROOF_HTTP_CODE_REMOTE=""
PROOF_BROWSE_MODE=""
PRECHECK_HTTP_CODE=""
PRECHECK_HTTP_CODE_LOCAL=""
PRECHECK_HTTP_CODE_REMOTE=""
PRECHECK_BROWSE_MODE=""
ERROR_CLASS=""
LAST_RUN_STATUS=""
trigger_message=""

resolve_run_artifact_dir() {
  local run_id="$1"
  local rtd_browse_file="$PROOF_DIR/rtd_browse.json"

  browse_dir_entries "soma_kajabi/run_to_done" "auto" "$rtd_browse_file" || true
  RUN_DIR_BROWSE_HTTP_CODE="$BROWSE_LAST_HTTP_CODE"
  RUN_DIR_BROWSE_HTTP_CODE_LOCAL="$BROWSE_LAST_HTTP_CODE_LOCAL"
  RUN_DIR_BROWSE_HTTP_CODE_REMOTE="$BROWSE_LAST_HTTP_CODE_REMOTE"
  RUN_DIR_BROWSE_MODE="$BROWSE_LAST_MODE"
  RUN_DIR_BROWSE_URL="$BROWSE_LAST_URL"
  RUN_DIR_BROWSE_REMOTE_URL="$BROWSE_LAST_REMOTE_URL"

  if [ "$RUN_DIR_BROWSE_HTTP_CODE" != "200" ]; then
    RUN_ARTIFACT_DIR=""
    RUN_ARTIFACT_DIR_ERROR="run_to_done browse failed: http_code=${RUN_DIR_BROWSE_HTTP_CODE}, mode=${RUN_DIR_BROWSE_MODE}"
    return 1
  fi

  rtd_fields=()
  while IFS= read -r line; do rtd_fields+=("$line"); done < <(python3 - "$rtd_browse_file" "$run_id" <<'PY'
import sys
from pathlib import Path

from ops.lib.aiops_remote_helpers import parse_browse_dir_entries, resolve_run_to_done_dir

body = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
entries = parse_browse_dir_entries(body)
result = resolve_run_to_done_dir(sys.argv[2], entries)
print(result.get("resolved_dir") or "")
print(result.get("error") or "")
PY
)

  RUN_ARTIFACT_DIR="${rtd_fields[0]:-}"
  RUN_ARTIFACT_DIR_ERROR="${rtd_fields[1]:-}"
  [ -n "$RUN_ARTIFACT_DIR" ]
}

decode_browse_json_payload() {
  local browse_file="$1"
  local payload_file="$2"
  python3 - "$browse_file" "$payload_file" <<'PY'
import sys
from pathlib import Path

from ops.lib.aiops_remote_helpers import parse_artifact_browse_proof, write_json_file

body = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
payload = parse_artifact_browse_proof(body)
if isinstance(payload, dict):
    write_json_file(Path(sys.argv[2]), payload)
    print("1")
else:
    print("0")
PY
}

fetch_precheck_payload() {
  local precheck_rel_path precheck_ok
  precheck_rel_path="${RUN_ARTIFACT_DIR#artifacts/}/PRECHECK.json"

  browse_dir_entries "$precheck_rel_path" "auto" "$PRECHECK_BROWSE_FILE" || true
  PRECHECK_HTTP_CODE="$BROWSE_LAST_HTTP_CODE"
  PRECHECK_HTTP_CODE_LOCAL="$BROWSE_LAST_HTTP_CODE_LOCAL"
  PRECHECK_HTTP_CODE_REMOTE="$BROWSE_LAST_HTTP_CODE_REMOTE"
  PRECHECK_BROWSE_MODE="$BROWSE_LAST_MODE"

  rm -f "$PRECHECK_PAYLOAD_FILE"
  if [ "$PRECHECK_HTTP_CODE" = "200" ]; then
    precheck_ok="$(decode_browse_json_payload "$PRECHECK_BROWSE_FILE" "$PRECHECK_PAYLOAD_FILE" || true)"
    [ "$precheck_ok" = "1" ] || rm -f "$PRECHECK_PAYLOAD_FILE"
  fi
}

fetch_proof_payload() {
  local proof_rel_path proof_ok
  proof_rel_path="${RUN_ARTIFACT_DIR#artifacts/}/PROOF.json"

  browse_dir_entries "$proof_rel_path" "auto" "$PROOF_BROWSE_FILE" || true
  PROOF_HTTP_CODE="$BROWSE_LAST_HTTP_CODE"
  PROOF_HTTP_CODE_LOCAL="$BROWSE_LAST_HTTP_CODE_LOCAL"
  PROOF_HTTP_CODE_REMOTE="$BROWSE_LAST_HTTP_CODE_REMOTE"
  PROOF_BROWSE_MODE="$BROWSE_LAST_MODE"

  rm -f "$PROOF_PAYLOAD_FILE"
  if [ "$PROOF_HTTP_CODE" = "200" ]; then
    proof_ok="$(decode_browse_json_payload "$PROOF_BROWSE_FILE" "$PROOF_PAYLOAD_FILE" || true)"
    [ "$proof_ok" = "1" ] || rm -f "$PROOF_PAYLOAD_FILE"
  fi
}

proof_payload_has_status() {
  python3 - "$1" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    print("0")
    raise SystemExit(0)
raw = path.read_text(encoding="utf-8", errors="replace").strip()
if not raw:
    print("0")
    raise SystemExit(0)
try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    print("0")
    raise SystemExit(0)
status = payload.get("status") if isinstance(payload, dict) else None
print("1" if isinstance(status, str) and status.strip() else "0")
PY
}

query_active_run_id() {
  local status_http active_id
  status_http="$("$CURL_BIN" -sS --connect-timeout 5 --max-time 20 \
    -o "$STATUS_FILE" -w "%{http_code}" \
    "${BASE_URL%/}/api/projects/soma_kajabi/status" || true)"
  [ -z "$status_http" ] && status_http="000"
  if [ "$status_http" != "200" ]; then
    echo ""
    return 0
  fi
  active_id="$(python3 - "$STATUS_FILE" <<'PY'
import sys
from pathlib import Path
from ops.lib.aiops_remote_helpers import parse_project_status_response
body = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
parsed = parse_project_status_response(body)
print(parsed.get("active_run_id") or "")
PY
)" || true
  echo "$active_id"
}

health_http_code="$("$CURL_BIN" -sS --connect-timeout 5 --max-time 15 -o "$HEALTH_FILE" -w "%{http_code}" "${BASE_URL%/}/api/ui/health_public" || true)"
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
RUN_ARTIFACT_DIR=""
RUN_ARTIFACT_DIR_ERROR=""
already_running_detected="false"
attached_run_id=""
attach_reason=""

if [ "$health_state" = "OK" ]; then
  mode_used="hq_api"
  log "HQ UI reachable. Triggering soma_run_to_done via /api/exec."
  trigger_http_code="$("$CURL_BIN" -sS --connect-timeout 5 --max-time 30 -X POST "${BASE_URL%/}/api/exec" -H "Content-Type: application/json" -d '{"action":"soma_run_to_done"}' -o "$TRIGGER_FILE" -w "%{http_code}" || true)"

  trigger_fields=()
  while IFS= read -r line; do trigger_fields+=("$line"); done < <(python3 - "$trigger_http_code" "$TRIGGER_FILE" <<'PY'
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

  if [ "$trigger_state" = "ALREADY_RUNNING" ]; then
    already_running_detected="true"
    log "ALREADY_RUNNING detected from trigger. Querying status endpoint."
    active_id="$(query_active_run_id)"
    if [ -z "$active_id" ] && [ -n "$remote_run_id" ]; then
      active_id="$remote_run_id"
      attach_reason="trigger_409_active_run_id"
    fi
    if [ -z "$active_id" ]; then
      log "No active run_id. Backing off up to ${ALREADY_RUNNING_BACKOFF_MAX:-180}s."
      _ar_elapsed=0
      _ar_wait=10
      while [ "$_ar_elapsed" -lt "${ALREADY_RUNNING_BACKOFF_MAX:-180}" ]; do
        sleep "$_ar_wait"
        _ar_elapsed=$((_ar_elapsed + _ar_wait))
        _ar_wait=$((_ar_wait + _ar_wait))
        [ "$_ar_wait" -gt 30 ] && _ar_wait=30
        active_id="$(query_active_run_id)"
        [ -n "$active_id" ] && break
      done
    fi
    if [ -z "$active_id" ]; then
      log "Retrying trigger once after backoff."
      retry_http="$("$CURL_BIN" -sS --connect-timeout 5 --max-time 30 -X POST \
        "${BASE_URL%/}/api/exec" -H "Content-Type: application/json" \
        -d '{"action":"soma_run_to_done"}' -o "$TRIGGER_FILE" -w "%{http_code}" || true)"
      retry_fields=()
      while IFS= read -r line; do retry_fields+=("$line"); done < <(python3 - "$retry_http" "$TRIGGER_FILE" <<'PY'
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
PY
)
      retry_run_id="${retry_fields[1]:-}"
      if [ -n "$retry_run_id" ]; then
        active_id="$retry_run_id"
        attach_reason="trigger_retry"
      fi
    fi
    if [ -n "$active_id" ]; then
      remote_run_id="$active_id"
      attached_run_id="$active_id"
      [ -z "$attach_reason" ] && attach_reason="status_endpoint"
      log "Attached to active run $remote_run_id (reason=$attach_reason)"
    else
      terminal_status="FAIL"
      ERROR_CLASS="ALREADY_RUNNING_NO_ACTIVE_ID"
      trigger_message="ALREADY_RUNNING: no active run_id after backoff+retry"
      log "FAIL: $trigger_message"
    fi
  fi

  if [ "$ERROR_CLASS" = "ALREADY_RUNNING_NO_ACTIVE_ID" ]; then
    : # Terminal failure — skip polling
  elif [ "$trigger_state" = "FAILED" ] || [ -z "$remote_run_id" ]; then
    log "Trigger via HQ failed ($trigger_state). Falling back to remote SSH."
    mode_used="remote_ssh"
  else
    _ar_poll_attempt=0
    while true; do
    _ar_poll_attempt=$((_ar_poll_attempt + 1))
    if [ "$_ar_poll_attempt" -gt 1 ]; then
      RUN_ARTIFACT_DIR=""
      RUN_ARTIFACT_DIR_ERROR=""
      ERROR_CLASS=""
      LAST_RUN_STATUS=""
      rm -f "$PROOF_PAYLOAD_FILE" "$PRECHECK_PAYLOAD_FILE" 2>/dev/null || true
    fi
    log "Polling /api/runs?id=$remote_run_id"
    deadline_epoch=$(( $(date +%s) + POLL_TIMEOUT_SEC ))
    while true; do
      now_epoch="$(date +%s)"
      if [ "$now_epoch" -ge "$deadline_epoch" ]; then
        terminal_status="FAIL"
        ERROR_CLASS="POLL_TIMEOUT"
        log "Polling timeout for run_id=$remote_run_id"
        break
      fi

      run_http_code="$("$CURL_BIN" -sS --connect-timeout 5 --max-time 20 -o "$RUN_FILE" -w "%{http_code}" "${BASE_URL%/}/api/runs?id=${remote_run_id}" || true)"
      if [ "$run_http_code" != "200" ]; then
        sleep "$POLL_INTERVAL_SEC"
        continue
      fi

      run_fields=()
      while IFS= read -r line; do run_fields+=("$line"); done < <(python3 - "$RUN_FILE" <<'PY'
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
      LAST_RUN_STATUS="$run_status"

      if [ -z "$RUN_ARTIFACT_DIR" ] && [ -n "$remote_run_id" ]; then
        resolve_run_artifact_dir "$remote_run_id" || true
        if [ -n "$RUN_ARTIFACT_DIR" ]; then
          log "resolved run_to_done dir: $RUN_ARTIFACT_DIR (browse_mode=$RUN_DIR_BROWSE_MODE http=${RUN_DIR_BROWSE_HTTP_CODE})"
        fi
      fi

      if [ -n "$RUN_ARTIFACT_DIR" ]; then
        if [ ! -s "$PROOF_PAYLOAD_FILE" ]; then
          fetch_proof_payload
          if [ "$PROOF_HTTP_CODE" != "200" ] || [ ! -s "$PROOF_PAYLOAD_FILE" ]; then
            fetch_precheck_payload
          fi
        fi
      fi

      if [ ! -f "$PROOF_PAYLOAD_FILE" ]; then
        : > "$PROOF_PAYLOAD_FILE"
      fi

      terminal_fields=()
      while IFS= read -r line; do terminal_fields+=("$line"); done < <(python3 - "$run_status" "$PROOF_PAYLOAD_FILE" "$BASE_URL" <<'PY'
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

    if [ -z "$RUN_ARTIFACT_DIR" ] && [ -n "$remote_run_id" ]; then
      if [ "$RUN_DIR_BROWSE_HTTP_CODE_LOCAL" = "403" ] || [ "$RUN_DIR_BROWSE_HTTP_CODE" = "403" ] || [ "$RUN_DIR_BROWSE_HTTP_CODE_REMOTE" = "403" ]; then
        ERROR_CLASS="BROWSE_FORBIDDEN"
        [ "$RUN_DIR_BROWSE_HTTP_CODE_LOCAL" = "403" ] && RUN_DIR_BROWSE_HTTP_CODE="403"
      else
        ERROR_CLASS="RUN_ARTIFACT_DIR_UNRESOLVED"
      fi
      [ -z "$RUN_ARTIFACT_DIR_ERROR" ] && RUN_ARTIFACT_DIR_ERROR="run_to_done dir not resolved; run_id=$remote_run_id"
      RUN_ARTIFACT_DIR_ERROR="${RUN_ARTIFACT_DIR_ERROR}; browse_http_code=${RUN_DIR_BROWSE_HTTP_CODE}; mode=${RUN_DIR_BROWSE_MODE}"
      terminal_status="FAIL"
      log "FAIL-CLOSED: $RUN_ARTIFACT_DIR_ERROR"
    fi

    if [ -n "$RUN_ARTIFACT_DIR" ]; then
      # Always re-fetch PROOF.json after poll completes to get terminal state
      # (early fetch during polling may have captured status=RUNNING)
      fetch_proof_payload
      proof_has_status="$(proof_payload_has_status "$PROOF_PAYLOAD_FILE")"
      if [ "$PROOF_HTTP_CODE" != "200" ] || [ ! -s "$PROOF_PAYLOAD_FILE" ] || [ "$proof_has_status" != "1" ]; then
        fetch_precheck_payload
        terminal_status="FAIL"
        [ -z "$ERROR_CLASS" ] && ERROR_CLASS="PROOF_MISSING_FOR_RUN"
        RUN_ARTIFACT_DIR_ERROR="PROOF.json missing or invalid for resolved run artifact dir; resolved_dir=${RUN_ARTIFACT_DIR}; proof_http_code=${PROOF_HTTP_CODE:-000}"
        log "FAIL-CLOSED: $RUN_ARTIFACT_DIR_ERROR"
      fi
    fi

    if [ -n "$RUN_ARTIFACT_DIR" ] && [ -z "$ERROR_CLASS" ]; then
      proof_derived_status="$(python3 - "$PROOF_PAYLOAD_FILE" "$PRECHECK_PAYLOAD_FILE" <<'PY'
import json, sys
from pathlib import Path

def get_status(p):
    if not p.exists():
        return ""
    raw = p.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    s = data.get("status") if isinstance(data, dict) else None
    return s.strip().upper() if isinstance(s, str) and s.strip() else ""

status = get_status(Path(sys.argv[1])) or get_status(Path(sys.argv[2]))
print(status or "")
PY
)"
      case "$proof_derived_status" in
        SUCCESS)
          terminal_status="SUCCESS" ;;
        WAITING_FOR_HUMAN)
          terminal_status="WAITING_FOR_HUMAN" ;;
        FAIL|FAILURE)
          terminal_status="FAIL" ;;
        ALREADY_RUNNING)
          terminal_status="FAIL"
          [ -z "$ERROR_CLASS" ] && ERROR_CLASS="ALREADY_RUNNING"
          ;;
        RUNNING)
          terminal_status="FAIL"
          [ -z "$ERROR_CLASS" ] && ERROR_CLASS="PROOF_STILL_RUNNING"
          ;;
        "")
          terminal_status="FAIL"
          [ -z "$ERROR_CLASS" ] && ERROR_CLASS="PROOF_STATUS_MISSING"
          ;;
        *)
          terminal_status="FAIL"
          [ -z "$ERROR_CLASS" ] && ERROR_CLASS="PROOF_STATUS_INVALID"
          ;;
      esac
    fi

    if [ "$terminal_status" = "FAIL" ] && [ -z "$ERROR_CLASS" ]; then
      ERROR_CLASS="$(python3 - "$PROOF_PAYLOAD_FILE" "$PRECHECK_PAYLOAD_FILE" <<'PY'
import json
import sys
from pathlib import Path

def pick(path: Path) -> str:
    if not path.exists():
        return ""
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if isinstance(data, dict):
        value = data.get("error_class")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""

proof = pick(Path(sys.argv[1]))
precheck = pick(Path(sys.argv[2]))
print(proof or precheck or "")
PY
)"
    fi

    if [ "$ERROR_CLASS" = "ALREADY_RUNNING" ] && [ "$_ar_poll_attempt" -le 1 ]; then
      already_running_detected="true"
      log "PROOF reports ALREADY_RUNNING. Querying status for active run."
      active_id="$(query_active_run_id)"
      if [ -n "$active_id" ]; then
        remote_run_id="$active_id"
        attached_run_id="$active_id"
        attach_reason="proof_already_running_reattach"
        log "Re-attaching to run $remote_run_id for re-poll"
        continue
      fi
    fi
    break
    done
  fi
else
  mode_used="remote_ssh"
  trigger_message="UI health state=$health_state"
fi

if [ "$mode_used" = "remote_ssh" ]; then
  log "Using SSH fallback: python3 ops/scripts/soma_run_to_done.py"
  ssh_rc=0
  "$SSH_BIN" "${SSH_OPTS[@]}" "$HOST" "set -euo pipefail; cd '$REPO_DIR'; python3 ops/scripts/soma_run_to_done.py" >"$REMOTE_OUTPUT_FILE" 2>>"$LOG_FILE" || ssh_rc=$?

  ssh_fields=()
  while IFS= read -r line; do ssh_fields+=("$line"); done < <(python3 - "$REMOTE_OUTPUT_FILE" "$BASE_URL" "$ssh_rc" <<'PY'
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
  python3 - "$RESULT_FILE" "$LOCAL_RUN_ID" "$mode_used" "$terminal_status" "$novnc_url" "$remote_run_id" "$remote_payload_json" "$trigger_message" "$ERROR_CLASS" "$RUN_DIR_BROWSE_HTTP_CODE" "$RUN_DIR_BROWSE_HTTP_CODE_LOCAL" "$RUN_DIR_BROWSE_HTTP_CODE_REMOTE" "$RUN_DIR_BROWSE_MODE" "$RUN_DIR_BROWSE_URL" "$RUN_DIR_BROWSE_REMOTE_URL" "$RUN_ARTIFACT_DIR_ERROR" "$PROOF_HTTP_CODE" "$PRECHECK_HTTP_CODE" "$already_running_detected" "$attached_run_id" "$attach_reason" <<'PY'
import json
import sys
from pathlib import Path

from ops.lib.aiops_remote_helpers import utc_now_iso, write_json_file

remote_payload = json.loads(sys.argv[7]) if sys.argv[7] else {}
payload = {
    "local_run_id": sys.argv[2],
    "mode": sys.argv[3],
    "terminal_status": sys.argv[4],
    "novnc_url": sys.argv[5] or None,
    "remote_run_id": sys.argv[6] or None,
    "remote_payload": remote_payload,
    "error_class": (
      sys.argv[9]
      or (remote_payload.get("error_class") if isinstance(remote_payload, dict) else None)
      or None
    ),
    "novnc_readiness_artifact_dir": (
      remote_payload.get("novnc_readiness_artifact_dir")
      or remote_payload.get("readiness_artifact_dir")
    ) if isinstance(remote_payload, dict) else None,
    "summary": sys.argv[8] or None,
    "run_artifact_dir_resolved": None,
    "run_artifact_dir_resolution_error": sys.argv[16] or None,
    "browse_http_code": sys.argv[10] or None,
    "browse_http_code_local": sys.argv[11] or None,
    "browse_http_code_remote": sys.argv[12] or None,
    "browse_mode": sys.argv[13] or None,
    "browse_url": sys.argv[14] or None,
    "browse_remote_url": sys.argv[15] or None,
    "proof_http_code": sys.argv[17] or None,
    "precheck_http_code": sys.argv[18] or None,
    "already_running_detected": sys.argv[19] == "true",
    "attached_run_id": sys.argv[20] or None,
    "attach_reason": sys.argv[21] or None,
    "finished_at": utc_now_iso(),
}
write_json_file(Path(sys.argv[1]), payload)
PY
else
  python3 - "$RESULT_FILE" "$LOCAL_RUN_ID" "$mode_used" "$terminal_status" "$novnc_url" "$remote_run_id" "$trigger_message" "$PROOF_PAYLOAD_FILE" "$PRECHECK_PAYLOAD_FILE" "$RUN_ARTIFACT_DIR" "$RUN_ARTIFACT_DIR_ERROR" "$ERROR_CLASS" "$RUN_DIR_BROWSE_HTTP_CODE" "$RUN_DIR_BROWSE_HTTP_CODE_LOCAL" "$RUN_DIR_BROWSE_HTTP_CODE_REMOTE" "$RUN_DIR_BROWSE_MODE" "$RUN_DIR_BROWSE_URL" "$RUN_DIR_BROWSE_REMOTE_URL" "$PROOF_HTTP_CODE" "$PRECHECK_HTTP_CODE" "$already_running_detected" "$attached_run_id" "$attach_reason" <<'PY'
import sys
import json
from pathlib import Path

from ops.lib.aiops_remote_helpers import utc_now_iso, write_json_file

proof_payload = {}
precheck_payload = {}
proof_path = Path(sys.argv[8])
precheck_path = Path(sys.argv[9])
if proof_path.exists():
    raw = proof_path.read_text(encoding="utf-8", errors="replace").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                proof_payload = parsed
        except json.JSONDecodeError:
            proof_payload = {}
if precheck_path.exists():
    raw = precheck_path.read_text(encoding="utf-8", errors="replace").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                precheck_payload = parsed
        except json.JSONDecodeError:
            precheck_payload = {}

novnc_readiness_artifact_dir = None
for source in (proof_payload, precheck_payload):
    if isinstance(source, dict):
        candidate = source.get("novnc_readiness_artifact_dir") or source.get("readiness_artifact_dir")
        if isinstance(candidate, str) and candidate.strip():
            novnc_readiness_artifact_dir = candidate.strip()
            break

payload = {
    "local_run_id": sys.argv[2],
    "mode": sys.argv[3],
    "terminal_status": sys.argv[4],
    "novnc_url": sys.argv[5] or None,
    "remote_run_id": sys.argv[6] or None,
    "summary": sys.argv[7] or None,
    "error_class": (
      sys.argv[12]
      or (proof_payload.get("error_class") if isinstance(proof_payload, dict) else None)
      or (precheck_payload.get("error_class") if isinstance(precheck_payload, dict) else None)
      or None
    ),
    "proof_payload": proof_payload or None,
    "precheck_payload": precheck_payload or None,
    "novnc_readiness_artifact_dir": novnc_readiness_artifact_dir,
    "run_artifact_dir_resolved": sys.argv[10] or None,
    "run_artifact_dir_resolution_error": sys.argv[11] or None,
    "browse_http_code": sys.argv[13] or None,
    "browse_http_code_local": sys.argv[14] or None,
    "browse_http_code_remote": sys.argv[15] or None,
    "browse_mode": sys.argv[16] or None,
    "browse_url": sys.argv[17] or None,
    "browse_remote_url": sys.argv[18] or None,
    "proof_http_code": sys.argv[19] or None,
    "precheck_http_code": sys.argv[20] or None,
    "already_running_detected": sys.argv[21] == "true",
    "attached_run_id": sys.argv[22] or None,
    "attach_reason": sys.argv[23] or None,
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

novnc_readiness_artifact_dir="$(python3 - "$RESULT_FILE" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.exists():
    print("")
    raise SystemExit(0)
try:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
except json.JSONDecodeError:
    print("")
    raise SystemExit(0)

def pick(d):
    for key in ("novnc_readiness_artifact_dir", "readiness_artifact_dir", "artifact_dir"):
        v = d.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""

for key in ("proof_payload", "precheck_payload", "remote_payload"):
    obj = data.get(key)
    if isinstance(obj, dict):
        val = pick(obj)
        if val:
            print(val)
            raise SystemExit(0)
print(pick(data))
PY
)"

echo "FAIL: soma_run_to_done did not complete successfully. Proof: $RESULT_FILE"
if [ -n "$novnc_readiness_artifact_dir" ]; then
  echo "noVNC readiness artifacts: $novnc_readiness_artifact_dir"
fi
exit 1

#!/usr/bin/env bash
# Hermetic selftest: aiops_soma_run_to_done.sh handles trigger 409 ALREADY_RUNNING
# by querying the status endpoint and attaching to the active run instead of failing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET="$ROOT_DIR/ops/remote/aiops_soma_run_to_done.sh"

echo "==> aiops_soma_already_running_selftest"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

MOCK_BIN="$TMP_DIR/bin"
mkdir -p "$MOCK_BIN"

ACTIVE_RUN_ID="20260304020000-xyzw"

cat >"$MOCK_BIN/curl" <<'MOCKCURL'
#!/usr/bin/env bash
set -euo pipefail

out_file=""
write_fmt=""
url=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    -o)
      out_file="${2:-}"
      shift 2
      ;;
    -w)
      write_fmt="${2:-}"
      shift 2
      ;;
    -X|-H|-d|--connect-timeout|--max-time)
      shift 2
      ;;
    -s|-S|-sS)
      shift
      ;;
    *)
      url="$1"
      shift
      ;;
  esac
done

body='{"ok":false}'
code="500"
case "$url" in
  *"/api/ui/health_public")
    body='{"ok": true, "build_sha": "selftest"}'
    code="200"
    ;;
  *"/api/exec")
    body='{"error_class":"ALREADY_RUNNING","active_run_id":"20260304020000-xyzw"}'
    code="409"
    ;;
  *"/api/projects/soma_kajabi/status")
    body='{"active_run_id":"20260304020000-xyzw"}'
    code="200"
    ;;
  *"/api/runs?id="*)
    body='{"ok":true,"run":{"status":"success","run_id":"20260304020000-xyzw"}}'
    code="200"
    ;;
  *"/api/artifacts/browse"*"PROOF"*)
    body='{"content":"{\"status\":\"SUCCESS\"}"}'
    code="200"
    ;;
  *"/api/artifacts/browse"*"PRECHECK"*)
    body='{"content":"{\"status\":\"SUCCESS\"}"}'
    code="200"
    ;;
  *"/api/artifacts/browse"*)
    body='{"entries":[{"name":"run_to_done_20260304T020000Z_abcd1234","type":"dir"}]}'
    code="200"
    ;;
esac

if [ -n "$out_file" ]; then
  printf '%s' "$body" >"$out_file"
else
  printf '%s' "$body"
fi

if [ -n "$write_fmt" ]; then
  printf '%s' "${write_fmt//\%\{http_code\}/$code}"
fi
MOCKCURL
chmod +x "$MOCK_BIN/curl"

cat >"$MOCK_BIN/ssh" <<'MOCKSSH'
#!/usr/bin/env bash
echo "ERROR: SSH should not be called for ALREADY_RUNNING attach" >&2
exit 99
MOCKSSH
chmod +x "$MOCK_BIN/ssh"

OUT_LOG="$TMP_DIR/run.log"
RC=0
(
  cd "$ROOT_DIR"
  AIOPS_CURL_BIN="$MOCK_BIN/curl" \
  AIOPS_SSH_BIN="$MOCK_BIN/ssh" \
  "$TARGET"
) >"$OUT_LOG" 2>&1 || RC=$?

if [ "$RC" -ne 0 ]; then
  echo "  FAIL: expected exit 0 (SUCCESS) for ALREADY_RUNNING attach, got $RC" >&2
  cat "$OUT_LOG" >&2
  exit 1
fi

RESULT_PATH="$(sed -n 's/^.*Proof: //p' "$OUT_LOG" | tail -n 1)"
if [ -z "$RESULT_PATH" ] || [ ! -f "$RESULT_PATH" ]; then
  echo "  FAIL: could not locate result JSON from output" >&2
  cat "$OUT_LOG" >&2
  exit 1
fi

python3 - "$RESULT_PATH" "$ACTIVE_RUN_ID" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected_run_id = sys.argv[2]

errors = []
if data.get("terminal_status") != "SUCCESS":
    errors.append(f"terminal_status={data.get('terminal_status')!r}, expected SUCCESS")
if data.get("already_running_detected") is not True:
    errors.append(f"already_running_detected={data.get('already_running_detected')!r}, expected True")
if data.get("attached_run_id") != expected_run_id:
    errors.append(f"attached_run_id={data.get('attached_run_id')!r}, expected {expected_run_id!r}")
if not data.get("attach_reason"):
    errors.append(f"attach_reason={data.get('attach_reason')!r}, expected non-empty")
if data.get("error_class") is not None:
    errors.append(f"error_class={data.get('error_class')!r}, expected None")
if errors:
    print("Result JSON validation failures:", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    print(f"Full result: {json.dumps(data, indent=2)}", file=sys.stderr)
    sys.exit(1)
PY

echo "  PASS: trigger 409 ALREADY_RUNNING attached to active run and completed SUCCESS"
echo "==> aiops_soma_already_running_selftest PASS"

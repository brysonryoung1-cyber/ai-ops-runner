#!/usr/bin/env bash
# Hermetic selftest: aiops_soma_run_to_done.sh must fail-closed when browse resolution is forbidden.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET="$ROOT_DIR/ops/remote/aiops_soma_run_to_done.sh"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

MOCK_BIN="$TMP_DIR/bin"
mkdir -p "$MOCK_BIN"

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
    body='{"run_id":"20260304010101-abcd"}'
    code="202"
    ;;
  *"/api/runs?id="*)
    body='{"ok":true,"run":{"status":"success","artifact_dir":"artifacts/hostd/unused"}}'
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
exit 99
MOCKSSH
chmod +x "$MOCK_BIN/ssh"

OUT_LOG="$TMP_DIR/run.log"
RC=0
(
  cd "$ROOT_DIR"
  AIOPS_CURL_BIN="$MOCK_BIN/curl" \
  AIOPS_SSH_BIN="$MOCK_BIN/ssh" \
  AIOPS_BROWSE_MOCK_HTTP_CODE="403" \
  AIOPS_BROWSE_MOCK_BODY_JSON='{"error":"forbidden"}' \
  AIOPS_BROWSE_SKIP_REMOTE_FALLBACK="1" \
  "$TARGET"
) >"$OUT_LOG" 2>&1 || RC=$?

if [ "$RC" -eq 0 ]; then
  echo "FAIL: expected non-zero exit for unresolved run artifact dir" >&2
  cat "$OUT_LOG" >&2
  exit 1
fi

RESULT_PATH="$(sed -n 's/^.*Proof: //p' "$OUT_LOG" | tail -n 1)"
if [ -z "$RESULT_PATH" ] || [ ! -f "$RESULT_PATH" ]; then
  echo "FAIL: could not locate result JSON path from output" >&2
  cat "$OUT_LOG" >&2
  exit 1
fi

python3 - "$RESULT_PATH" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert data.get("terminal_status") == "FAIL", data
assert data.get("run_artifact_dir_resolved") is None, data
assert data.get("error_class") in {"BROWSE_FORBIDDEN", "RUN_ARTIFACT_DIR_UNRESOLVED"}, data
assert data.get("run_artifact_dir_resolution_error"), data
assert str(data.get("browse_http_code") or "") in {"403", "000"}, data
assert data.get("browse_mode") in {"local", "remote_fallback"}, data
PY

echo "==> aiops_soma_run_to_done_fail_closed_selftest PASS"

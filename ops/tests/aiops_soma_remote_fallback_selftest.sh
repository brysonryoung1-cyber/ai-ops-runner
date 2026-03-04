#!/usr/bin/env bash
# Hermetic selftest: local browse 403 + remote_fallback 200 → BROWSE_LAST_HTTP_CODE=200, mode=remote_fallback.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET="$ROOT_DIR/ops/remote/aiops_soma_run_to_done.sh"

echo "==> aiops_soma_remote_fallback_selftest"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

MOCK_BIN="$TMP_DIR/bin"
mkdir -p "$MOCK_BIN"

# --- mock curl: health 200, trigger 202, runs poll 200 ---
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
    body='{"ok":true,"run":{"status":"success","artifact_dir":"artifacts/soma_kajabi/run_to_done/run_to_done_20260304T010101Z_abcd"}}'
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

# --- mock ssh (unused — remote_fallback uses mock env vars) ---
cat >"$MOCK_BIN/ssh" <<'MOCKSSH'
#!/usr/bin/env bash
exit 99
MOCKSSH
chmod +x "$MOCK_BIN/ssh"

# Mock body: valid directory listing for run_to_done dir resolution
REMOTE_MOCK_DIR_BODY='{"entries":[{"name":"run_to_done_20260304T010101Z_abcd","type":"dir"}]}'
# Mock body: valid PROOF.json browse response
REMOTE_MOCK_PROOF_BODY='{"content":"{\"status\":\"SUCCESS\"}"}'

# We need different mock bodies for different browse calls. Since
# AIOPS_BROWSE_REMOTE_MOCK_BODY_JSON is global, we use a body file
# that we swap between calls. Set the initial body for directory listing.
MOCK_BODY_FILE="$TMP_DIR/remote_mock_body.json"
printf '%s' "$REMOTE_MOCK_DIR_BODY" >"$MOCK_BODY_FILE"

# Create a wrapper that swaps the body file after the first browse call
# so the PROOF fetch gets the correct body.
SWAP_SCRIPT="$TMP_DIR/swap_body.sh"
cat >"$SWAP_SCRIPT" <<SWAP
#!/usr/bin/env bash
printf '%s' '$REMOTE_MOCK_PROOF_BODY' >"$MOCK_BODY_FILE"
SWAP
chmod +x "$SWAP_SCRIPT"

OUT_LOG="$TMP_DIR/run.log"
RC=0
(
  cd "$ROOT_DIR"
  AIOPS_CURL_BIN="$MOCK_BIN/curl" \
  AIOPS_SSH_BIN="$MOCK_BIN/ssh" \
  AIOPS_BROWSE_MOCK_HTTP_CODE="403" \
  AIOPS_BROWSE_MOCK_BODY_JSON='{"error":"forbidden"}' \
  AIOPS_BROWSE_REMOTE_MOCK_HTTP_CODE="200" \
  AIOPS_BROWSE_REMOTE_MOCK_BODY_FILE="$MOCK_BODY_FILE" \
  "$TARGET"
) >"$OUT_LOG" 2>&1 || RC=$?

RESULT_PATH="$(sed -n 's/^.*Proof: //p' "$OUT_LOG" | tail -n 1)"
if [ -z "$RESULT_PATH" ] || [ ! -f "$RESULT_PATH" ]; then
  echo "  FAIL: could not locate result JSON from output" >&2
  cat "$OUT_LOG" >&2
  exit 1
fi

python3 - "$RESULT_PATH" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

browse_code = str(data.get("browse_http_code") or "")
browse_local = str(data.get("browse_http_code_local") or "")
browse_remote = str(data.get("browse_http_code_remote") or "")
browse_mode = data.get("browse_mode") or ""

errors = []
if browse_code != "200":
    errors.append(f"browse_http_code={browse_code!r}, expected '200'")
if browse_local != "403":
    errors.append(f"browse_http_code_local={browse_local!r}, expected '403'")
if browse_remote != "200":
    errors.append(f"browse_http_code_remote={browse_remote!r}, expected '200'")
if browse_mode != "remote_fallback":
    errors.append(f"browse_mode={browse_mode!r}, expected 'remote_fallback'")

if errors:
    print(f"ASSERTION FAILURES:\n" + "\n".join(f"  - {e}" for e in errors))
    print(f"Full result:\n{json.dumps(data, indent=2)}")
    sys.exit(1)

print(f"  browse_http_code={browse_code} browse_http_code_local={browse_local} "
      f"browse_http_code_remote={browse_remote} browse_mode={browse_mode}")
PY

echo "==> aiops_soma_remote_fallback_selftest PASS"

#!/usr/bin/env bash
# novnc_guard.sh — Self-healing guard for noVNC service (framebuffer-aware).
#
# Modes:
#   (default) FAST: service + ports + 1x ws handshake + minimal HTTP sanity; DEEP checks skipped.
#   --deep  DEEP framebuffer guard (service, Xvfb, x11vnc, websockify, framebuffer not-all-black)
#   --fast  explicit FAST mode
#
# Delegates to novnc_framebuffer_guard.sh (DEEP) or novnc_fast_precheck.sh (--fast).
# Writes JSON report to artifacts/hq_audit/novnc_guard/<run_id>/status.json.
# Exit: 0 if pass (or remediated), nonzero if fail-closed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_novnc}"
NOVNC_PORT="${OPENCLAW_NOVNC_PORT:-6080}"
REPORT_DIR="$ROOT_DIR/artifacts/hq_audit/novnc_guard/$RUN_ID"
FB_GUARD="$SCRIPT_DIR/novnc_framebuffer_guard.sh"
FAST_PRECHECK="${OPENCLAW_NOVNC_FAST_PRECHECK:-$ROOT_DIR/ops/scripts/novnc_fast_precheck.sh}"
MODE="fast"

mkdir -p "$REPORT_DIR"

case "${1:-}" in
  ""|--fast)
    MODE="fast"
    ;;
  --deep)
    MODE="deep"
    ;;
  *)
    echo "novnc_guard: unknown argument '$1' (expected --fast or --deep)" >&2
    exit 2
    ;;
esac

deep_skip_status="SKIP_DEEP_NOT_REQUESTED"
if ! pgrep -f "Xvfb" >/dev/null 2>&1; then
  deep_skip_status="SKIP_DEEP_XVFB_MISSING"
fi

# FAST mode: service + ports + 1x ws handshake + minimal HTTP sanity (≤15s)
if [ "$MODE" = "fast" ]; then
  if [ -x "$FAST_PRECHECK" ]; then
    if OPENCLAW_RUN_ID="$RUN_ID" "$FAST_PRECHECK"; then
      python3 -c "
import json
from datetime import datetime, timezone
d = {
  'status': 'PASS_FAST',
  'run_id': '$RUN_ID',
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
  'service_active': True,
  'vnc_html_ok': True,
  'framebuffer_ok': None,
  'remediated': False,
  'novnc_port': $NOVNC_PORT,
  'checks': ['service_active', 'ports_listening', 'ws_handshake', 'vnc_html'],
  'deep_status': '$deep_skip_status',
  'mode': 'fast',
}
with open('$REPORT_DIR/status.json', 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null || true
      echo "PASS_FAST: service_active, ports_listening, ws_handshake, vnc_html"
      echo "$deep_skip_status"
      exit 0
    fi
  fi
  python3 -c "
import json
from datetime import datetime, timezone
d = {
  'status': 'FAIL_FAST',
  'run_id': '$RUN_ID',
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
  'service_active': False,
  'vnc_html_ok': False,
  'framebuffer_ok': False,
  'remediated': False,
  'novnc_port': $NOVNC_PORT,
  'checks': ['service_active', 'ports_listening', 'ws_handshake', 'vnc_html'],
  'deep_status': '$deep_skip_status',
  'mode': 'fast',
}
with open('$REPORT_DIR/status.json', 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null || true
  echo "FAIL_FAST: noVNC fast precheck failed" >&2
  exit 1
fi

# DEEP mode: Run framebuffer-aware guard (handles heal, hard reset, fail-closed)
if [ -x "$FB_GUARD" ]; then
  result_file="$(mktemp)"
  if "$FB_GUARD" >"$result_file" 2>/dev/null; then
    remediated=false
    grep -q '"remediated":\s*true' "$result_file" 2>/dev/null && remediated=true
    python3 -c "
import json
from datetime import datetime, timezone
d = {
  'status': 'PASS_DEEP',
  'run_id': '$RUN_ID',
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
  'service_active': True,
  'vnc_html_ok': True,
  'framebuffer_ok': True,
  'remediated': $([ \"$remediated\" = true ] && echo True || echo False),
  'novnc_port': $NOVNC_PORT,
  'checks': ['service_active', 'xvfb', 'x11vnc', 'websockify', 'framebuffer_not_all_black'],
  'deep_status': 'PASS_DEEP',
  'mode': 'deep',
}
with open('$REPORT_DIR/status.json', 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null || true
    echo "PASS_DEEP: framebuffer guard passed"
    rm -f "$result_file"
    exit 0
  fi
  fail_reason="$(python3 -c "
import json,sys
try:
    d=json.load(open('$result_file'))
except Exception:
    print('deep_guard_failed')
    raise SystemExit(0)
print(d.get('fail_reason') or 'deep_guard_failed')
" 2>/dev/null || echo "deep_guard_failed")"
  python3 -c "
import json
from datetime import datetime, timezone
d = {
  'status': 'FAIL_DEEP',
  'run_id': '$RUN_ID',
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
  'service_active': False,
  'vnc_html_ok': False,
  'framebuffer_ok': False,
  'remediated': False,
  'novnc_port': $NOVNC_PORT,
  'checks': ['service_active', 'xvfb', 'x11vnc', 'websockify', 'framebuffer_not_all_black'],
  'deep_status': 'FAIL_DEEP',
  'mode': 'deep',
  'fail_reason': '$fail_reason',
}
with open('$REPORT_DIR/status.json', 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null || true
  if [ "$fail_reason" = "xvfb_missing" ]; then
    echo "FAIL_DEEP_XVFB_MISSING: deep mode requires Xvfb/process stack" >&2
  else
    echo "FAIL_DEEP: $fail_reason" >&2
  fi
  rm -f "$result_file"
  exit 1
fi

# Fallback: framebuffer guard missing or failed — write fail status
python3 -c "
import json
from datetime import datetime, timezone
d = {
  'status': 'FAIL_DEEP',
  'run_id': '$RUN_ID',
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
  'service_active': False,
  'vnc_html_ok': False,
  'framebuffer_ok': False,
  'remediated': False,
  'novnc_port': $NOVNC_PORT,
  'deep_status': 'FAIL_DEEP',
  'mode': 'deep',
  'fail_reason': 'framebuffer_guard_missing',
}
with open('$REPORT_DIR/status.json', 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null || true
echo "FAIL_DEEP: novnc_framebuffer_guard missing" >&2
exit 1

#!/usr/bin/env bash
# novnc_fast_precheck.sh — FAST noVNC readiness (≤15s).
#
# Checks (all required):
#   - openclaw-novnc.service is-active
#   - Ports listening: 6080 (websockify), 5900 (x11vnc)
#   - HTTP GET vnc.html returns 200
#   - Single local WS handshake succeeds (1s hold, not 10s)
#
# Writes: artifacts/novnc_debug/<run_id>/{timings.json, ws_check.json}
# Exit: 0 if PASS, 1 if FAIL.
# Env: OPENCLAW_RUN_ID, OPENCLAW_NOVNC_PORT (6080), OPENCLAW_NOVNC_VNC_PORT (5900)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_fast}"
NOVNC_PORT="${OPENCLAW_NOVNC_PORT:-6080}"
VNC_PORT="${OPENCLAW_NOVNC_VNC_PORT:-5900}"
ART_DIR="$ROOT_DIR/artifacts/novnc_debug/$RUN_ID"

mkdir -p "$ART_DIR"
TIMINGS_FILE="$ART_DIR/timings.json"
WS_CHECK_FILE="$ART_DIR/ws_check.json"

_start() { echo "$(date +%s.%N)"; }
_record() {
  local key="$1" start="$2" end="$3"
  local elapsed
  elapsed=$(python3 -c "print(round(float('$end') - float('$start'), 2))" 2>/dev/null || echo "0")
  echo "$key:$elapsed"
}

T0=$(_start)

# 1) Service active
if [ "$(systemctl is-active openclaw-novnc.service 2>/dev/null || echo inactive)" != "active" ]; then
  python3 -c "
import json
from datetime import datetime, timezone
d = {'ok': False, 'fail_reason': 'service_not_active', 'run_id': '$RUN_ID', 'timestamp_utc': datetime.now(timezone.utc).isoformat()}
with open('$TIMINGS_FILE', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null || true
  exit 1
fi
T1=$(_start)

# 2) Ports listening
if ! (ss -lntp 2>/dev/null | grep -q ":${NOVNC_PORT}" && ss -lntp 2>/dev/null | grep -q ":${VNC_PORT}"); then
  python3 -c "
import json
from datetime import datetime, timezone
d = {'ok': False, 'fail_reason': 'ports_not_listening', 'run_id': '$RUN_ID', 'timestamp_utc': datetime.now(timezone.utc).isoformat()}
with open('$TIMINGS_FILE', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null || true
  exit 1
fi
T2=$(_start)

# 3) HTTP vnc.html
if ! curl -fsS --connect-timeout 2 --max-time 4 "http://127.0.0.1:$NOVNC_PORT/vnc.html" >/dev/null 2>/dev/null; then
  python3 -c "
import json
from datetime import datetime, timezone
d = {'ok': False, 'fail_reason': 'vnc_html_fail', 'run_id': '$RUN_ID', 'timestamp_utc': datetime.now(timezone.utc).isoformat()}
with open('$TIMINGS_FILE', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null || true
  exit 1
fi
T3=$(_start)

# 4) Single WS handshake (1s hold)
OPENCLAW_WS_STABILITY_HOLD_SEC=1 OPENCLAW_NOVNC_PORT="$NOVNC_PORT" python3 "$SCRIPT_DIR/novnc_ws_stability_check.py" --local 2>/dev/null | tee "$WS_CHECK_FILE" | python3 -c "
import sys, json
d = json.load(sys.stdin)
sys.exit(0 if d.get('ok') else 1)
" 2>/dev/null || {
  python3 -c "
import json
from datetime import datetime, timezone
d = {'ok': False, 'fail_reason': 'ws_handshake_fail', 'run_id': '$RUN_ID', 'timestamp_utc': datetime.now(timezone.utc).isoformat()}
with open('$TIMINGS_FILE', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null || true
  exit 1
}
T4=$(_start)

# Write timings
python3 -c "
import json
from datetime import datetime, timezone
t0, t1, t2, t3, t4 = float('$T0'), float('$T1'), float('$T2'), float('$T3'), float('$T4')
d = {
  'ok': True,
  'run_id': '$RUN_ID',
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
  'timings_sec': {
    'service_check': round(t1 - t0, 2),
    'ports_check': round(t2 - t1, 2),
    'vnc_html': round(t3 - t2, 2),
    'ws_handshake': round(t4 - t3, 2),
    'total': round(t4 - t0, 2),
  },
}
with open('$TIMINGS_FILE', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null || true

exit 0

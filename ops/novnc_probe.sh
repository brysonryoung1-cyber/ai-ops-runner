#!/usr/bin/env bash
# novnc_probe.sh — Verify noVNC stack is connectable before WAITING_FOR_HUMAN.
#
# Checks:
#   a) HTTP GET http://127.0.0.1:6080/vnc.html returns 200
#   b) TCP connect to 127.0.0.1:<VNC_PORT> succeeds (x11vnc listening)
#   c) Optional: websocket endpoint responds (best-effort)
#
# Exit 0 if all required checks pass. Exit non-zero with single-line reason on failure.
# Env: OPENCLAW_NOVNC_PORT (default 6080), OPENCLAW_NOVNC_VNC_PORT (default 5900)
set -euo pipefail

WS_PORT="${OPENCLAW_NOVNC_PORT:-6080}"
VNC_PORT="${OPENCLAW_NOVNC_VNC_PORT:-5900}"

# a) HTTP GET vnc.html returns 200
_http_ok() {
  if command -v curl >/dev/null 2>&1; then
    code="$(curl -sf -o /dev/null -w "%{http_code}" "http://127.0.0.1:$WS_PORT/vnc.html" 2>/dev/null)"
    [ "$code" = "200" ]
  else
    OPENCLAW_PROBE_WS="$WS_PORT" python3 -c "
import urllib.request, os
port = os.environ.get('OPENCLAW_PROBE_WS', '6080')
try:
    r = urllib.request.urlopen('http://127.0.0.1:' + port + '/vnc.html', timeout=3)
    exit(0 if r.status == 200 else 1)
except Exception:
    exit(1)
" 2>/dev/null
  fi
}
if ! _http_ok; then
  echo "vnc.html not 200"
  exit 1
fi

# b) TCP connect to VNC port (x11vnc listening)
if ! python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
try:
    s.connect(('127.0.0.1', $VNC_PORT))
    s.close()
except OSError:
    exit(1)
" 2>/dev/null; then
  echo "vnc_port_not_listening"
  exit 1
fi

# c) Optional: websocket (best-effort; skip if curl lacks websocket support)
# We consider a) + b) sufficient for "noVNC READY"

exit 0

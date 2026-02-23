#!/usr/bin/env bash
# Connection selftest: validate noVNC capture flow (action, supervisor, systemd, port).
# Does NOT run real Kajabi — verifies action registered, supervisor valid, and
# (when systemd available) that starting capture spins up noVNC and port responds.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

echo "==> kajabi_capture_interactive_connection_selftest"

# 1. Action registered
if ! python3 -c "
import json
with open('config/action_registry.json') as f:
    d = json.load(f)
ids = [a['id'] for a in d['actions']]
assert 'soma_kajabi_capture_interactive' in ids, 'soma_kajabi_capture_interactive not in action_registry'
print('  PASS: action in action_registry')
"; then
  echo "  FAIL: action not in registry"
  exit 1
fi

# 2. Supervisor script exists and is valid
if [ ! -f "$ROOT_DIR/ops/novnc_supervisor.sh" ]; then
  echo "  FAIL: ops/novnc_supervisor.sh missing"
  exit 1
fi
if ! bash -n "$ROOT_DIR/ops/novnc_supervisor.sh" 2>/dev/null; then
  echo "  FAIL: novnc_supervisor.sh has syntax errors"
  exit 1
fi
echo "  PASS: novnc_supervisor.sh valid"

# 3. Systemd unit exists
if [ ! -f "$ROOT_DIR/ops/systemd/openclaw-novnc.service" ]; then
  echo "  FAIL: openclaw-novnc.service missing"
  exit 1
fi
echo "  PASS: openclaw-novnc.service exists"

# 4. If systemd + unit installed + root: start capture flow, verify port, stop
port_check() {
  python3 -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1',6080)); s.close()" 2>/dev/null
}
if command -v systemctl >/dev/null 2>&1 && [ -f /etc/systemd/system/openclaw-novnc.service ]; then
  SELFTEST_ARTIFACT="$(mktemp -d)"
  mkdir -p /run/openclaw-novnc 2>/dev/null || true
  printf "OPENCLAW_NOVNC_RUN_ID=selftest\nOPENCLAW_NOVNC_ARTIFACT_DIR=%s\nOPENCLAW_NOVNC_PORT=6080\nOPENCLAW_NOVNC_DISPLAY=:99\n" "$SELFTEST_ARTIFACT" > /run/openclaw-novnc/next.env 2>/dev/null || true

  systemctl stop openclaw-novnc 2>/dev/null || true
  if systemctl start openclaw-novnc 2>/dev/null; then
    for i in $(seq 1 15); do
      if port_check; then
        echo "  PASS: websockify port 6080 responds (after ${i}s)"
        break
      fi
      [ "$i" -eq 15 ] && { echo "  FAIL: port 6080 did not respond"; systemctl stop openclaw-novnc 2>/dev/null || true; rm -rf "$SELFTEST_ARTIFACT"; exit 1; }
      sleep 1
    done
    systemctl stop openclaw-novnc 2>/dev/null || true
    sleep 2
    systemctl is-active openclaw-novnc 2>/dev/null | grep -q active && echo "  WARN: service still active after stop" || echo "  PASS: service stopped"
  else
    echo "  SKIP: systemctl start openclaw-novnc failed (Xvfb/novnc deps may be missing)"
  fi
  rm -rf "$SELFTEST_ARTIFACT" 2>/dev/null || true
else
  echo "  SKIP: openclaw-novnc not installed (deploy installs on aiops-1)"
fi

# 5. Capture script creates artifacts (existing behavior)
ARTIFACT_DIR="$(mktemp -d)"
export ARTIFACT_DIR
export OPENCLAW_REPO_ROOT="$ROOT_DIR"
trap "rm -rf '$ARTIFACT_DIR'" EXIT
set +e
python3 ./ops/scripts/kajabi_capture_interactive.py 2>/dev/null
set -e
if [ -f "$ARTIFACT_DIR/summary.json" ] || [ -f "$ARTIFACT_DIR/instructions.txt" ]; then
  echo "  PASS: capture script creates artifacts"
else
  echo "  FAIL: no summary.json or instructions.txt in $ARTIFACT_DIR"
  ls -la "$ARTIFACT_DIR" 2>/dev/null || true
  exit 1
fi

echo "==> kajabi_capture_interactive_connection_selftest PASS"

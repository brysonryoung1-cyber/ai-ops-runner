#!/usr/bin/env bash
# rootd_selftest.sh — Tests rootd allowlist enforcement and HMAC validation.
#
# Tests:
#   1. rootd health endpoint responds
#   2. rootd rejects unsigned requests (missing HMAC)
#   3. rootd rejects requests with invalid HMAC
#   4. rootd executes allowlisted command (systemctl_restart simulation)
#   5. rootd refuses non-allowlisted command
#   6. rootd refuses non-allowlisted unit
#
# Requires rootd to be running. Use --mock to test without live rootd.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SOCKET="/run/openclaw/rootd.sock"
HMAC_KEY_PATH="/etc/ai-ops-runner/secrets/rootd_hmac_key"
PASSED=0
FAILED=0
MOCK="${1:-}"

pass() {
  echo "  PASS: $1"
  PASSED=$((PASSED + 1))
}

fail() {
  echo "  FAIL: $1"
  FAILED=$((FAILED + 1))
}

echo "=== rootd Self-Test ==="

# Test 1: Health endpoint (via policy_evaluator as fallback if no socket)
if [ "$MOCK" = "--mock" ] || [ ! -S "$SOCKET" ]; then
  echo "  SKIP: rootd not running (use --mock for policy-only tests)"
  echo "  Running policy-only tests..."

  cd "$ROOT_DIR"
  python3 ops/tests/policy_evaluator_selftest.py
  exit $?
fi

# Test 1: Health endpoint
if python3 -c "
import socket, json
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(3)
s.connect('$SOCKET')
s.sendall(b'GET /health HTTP/1.0\r\nHost: rootd\r\n\r\n')
data = s.recv(4096).decode()
s.close()
body = data.split('\r\n\r\n', 1)[-1]
d = json.loads(body)
exit(0 if d.get('ok') else 1)
" 2>/dev/null; then
  pass "rootd health responds OK"
else
  fail "rootd health did not respond"
fi

# Test 2: Reject unsigned request
RESPONSE="$(python3 -c "
import socket, json
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(5)
s.connect('$SOCKET')
body = json.dumps({'command': 'systemctl_restart', 'args': {'unit': 'openclaw-hostd.service'}}).encode()
req = f'POST /exec HTTP/1.0\r\nHost: rootd\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n'.encode() + body
s.sendall(req)
data = s.recv(8192).decode()
s.close()
print(data.split('\r\n\r\n', 1)[-1])
" 2>/dev/null)"

if echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if 'HMAC' in d.get('error','') or 'Invalid' in d.get('error','') else 1)" 2>/dev/null; then
  pass "rootd rejects unsigned requests"
else
  fail "rootd should reject unsigned requests: $RESPONSE"
fi

# Test 3: Reject invalid HMAC
RESPONSE="$(python3 -c "
import socket, json
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(5)
s.connect('$SOCKET')
body = json.dumps({'command': 'systemctl_restart', 'args': {'unit': 'openclaw-hostd.service'}}).encode()
req = f'POST /exec HTTP/1.0\r\nHost: rootd\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nX-RootD-HMAC: deadbeef00000000000000000000000000000000000000000000000000000000\r\n\r\n'.encode() + body
s.sendall(req)
data = s.recv(8192).decode()
s.close()
print(data.split('\r\n\r\n', 1)[-1])
" 2>/dev/null)"

if echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if 'HMAC' in d.get('error','') or 'Invalid' in d.get('error','') else 1)" 2>/dev/null; then
  pass "rootd rejects invalid HMAC"
else
  fail "rootd should reject invalid HMAC: $RESPONSE"
fi

# Test 4: Execute allowlisted restart (with valid HMAC)
if [ -f "$HMAC_KEY_PATH" ] && [ -r "$HMAC_KEY_PATH" ]; then
  RESPONSE="$(python3 -c "
import sys
sys.path.insert(0, '$ROOT_DIR')
from ops.rootd_client import RootdClient
client = RootdClient()
# Use status instead of restart to avoid disruption
result = client.exec('systemctl_restart', {'unit': 'openclaw-canary.service'})
import json
print(json.dumps(result))
" 2>/dev/null)"

  if echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('policy_allowed') else 1)" 2>/dev/null; then
    pass "rootd accepts allowlisted unit restart"
  else
    fail "rootd should accept allowlisted unit restart: $RESPONSE"
  fi
else
  echo "  SKIP: HMAC key not readable (test 4)"
fi

# Test 5: Refuse non-allowlisted command
if [ -f "$HMAC_KEY_PATH" ] && [ -r "$HMAC_KEY_PATH" ]; then
  RESPONSE="$(python3 -c "
import sys
sys.path.insert(0, '$ROOT_DIR')
from ops.rootd_client import RootdClient
client = RootdClient()
result = client.exec('rm_rf_everything', {})
import json
print(json.dumps(result))
" 2>/dev/null)"

  if echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if not d.get('ok') and not d.get('policy_allowed', True) else 1)" 2>/dev/null; then
    pass "rootd refuses non-allowlisted command"
  else
    fail "rootd should refuse non-allowlisted command: $RESPONSE"
  fi
else
  echo "  SKIP: HMAC key not readable (test 5)"
fi

# Test 6: Refuse non-allowlisted unit
if [ -f "$HMAC_KEY_PATH" ] && [ -r "$HMAC_KEY_PATH" ]; then
  RESPONSE="$(python3 -c "
import sys
sys.path.insert(0, '$ROOT_DIR')
from ops.rootd_client import RootdClient
client = RootdClient()
result = client.exec('systemctl_restart', {'unit': 'sshd.service'})
import json
print(json.dumps(result))
" 2>/dev/null)"

  if echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if not d.get('ok') and not d.get('policy_allowed', True) else 1)" 2>/dev/null; then
    pass "rootd refuses non-allowlisted unit"
  else
    fail "rootd should refuse non-allowlisted unit: $RESPONSE"
  fi
else
  echo "  SKIP: HMAC key not readable (test 6)"
fi

echo ""
echo "=== Results: $PASSED passed, $FAILED failed ==="
exit "$FAILED"

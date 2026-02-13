#!/usr/bin/env bash
# openclaw_doctor.sh — OpenClaw infrastructure health + audit checks
#
# Verifies:
#   1. Tailscale is up and connected.
#   2. Docker Compose stack is healthy (all services running).
#   3. Runner API healthz responds on 127.0.0.1:8000.
#   4. No ports are bound to 0.0.0.0 or [::] unexpectedly (fail if found).
#
# Exit codes:
#   0 = all checks passed
#   1 = one or more checks failed (fail-closed)
#
# Designed to run hourly via openclaw-doctor.timer.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

FAILURES=0
CHECKS=0

pass() { CHECKS=$((CHECKS + 1)); echo "  PASS: $1"; }
fail() { CHECKS=$((CHECKS + 1)); FAILURES=$((FAILURES + 1)); echo "  FAIL: $1" >&2; }

echo "=== openclaw_doctor.sh ==="
echo "  Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  Host: $(hostname)"
echo ""

# --- 1. Tailscale ---
echo "--- Tailscale ---"
if command -v tailscale >/dev/null 2>&1; then
  if tailscale status >/dev/null 2>&1; then
    pass "Tailscale is up"
  else
    fail "Tailscale is down or not connected"
  fi
else
  fail "tailscale command not found"
fi

# --- 2. Docker Compose stack ---
echo "--- Docker Compose ---"
if command -v docker >/dev/null 2>&1; then
  # Check that docker compose ps shows services and none are unhealthy/exited
  COMPOSE_STATUS="$(docker compose ps --format json 2>/dev/null || echo "")"
  if [ -z "$COMPOSE_STATUS" ]; then
    fail "docker compose ps returned no output (stack not running?)"
  else
    UNHEALTHY="$(echo "$COMPOSE_STATUS" | python3 -c "
import sys, json

raw = sys.stdin.read().strip()
if not raw:
    sys.exit(0)

# docker compose ps --format json may emit:
#   - one JSON object per line  (older docker compose)
#   - a single JSON array       (newer docker compose)
services = []
try:
    parsed = json.loads(raw)
    if isinstance(parsed, list):
        services = parsed
    elif isinstance(parsed, dict):
        services = [parsed]
except json.JSONDecodeError:
    # Fall back to line-by-line parsing
    for line in raw.split('\n'):
        line = line.strip()
        if not line:
            continue
        try:
            services.append(json.loads(line))
        except json.JSONDecodeError:
            continue

bad = []
for svc in services:
    if not isinstance(svc, dict):
        continue
    state = svc.get('State', '').lower()
    health = svc.get('Health', '').lower()
    name = svc.get('Name', svc.get('Service', 'unknown'))
    if state != 'running' or health == 'unhealthy':
        bad.append(f'{name}({state}/{health})')
if bad:
    print(' '.join(bad))
" 2>/dev/null || echo "parse-error")"
    if [ -z "$UNHEALTHY" ]; then
      pass "All Docker services healthy"
    else
      fail "Unhealthy services: $UNHEALTHY"
    fi
  fi
else
  fail "docker command not found"
fi

# --- 3. API healthz ---
echo "--- API healthz ---"
if curl -sf http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
  pass "API healthz OK (127.0.0.1:8000)"
else
  fail "API healthz FAILED (127.0.0.1:8000)"
fi

# --- 4. Public Port Audit (tailnet-aware) ---
echo "--- Public Port Audit ---"
# Tailnet-aware port policy (100.64.0.0/10 = Tailscale CGNAT range):
#   1. 127.0.0.1 / ::1         → always allowed (localhost)
#   2. 100.64.0.0/10            → PRIVATE (tailnet); allowed for any process
#   3. tailscaled / tailscale   → allowed on any address (DERP relay, etc.)
#   4. sshd on 0.0.0.0 / ::    → FAIL (must bind to tailnet IP only)
#   5. Any other on 0.0.0.0/:: → FAIL
if command -v ss >/dev/null 2>&1; then
  PORT_RESULT="$(ss -tlnp 2>/dev/null | python3 -c "
import sys, re

TAILNET_LO = (100 << 24) | (64 << 16)
TAILNET_HI = (100 << 24) | (127 << 16) | (255 << 8) | 255

def _ip2int(ip):
    p = ip.split('.')
    if len(p) != 4:
        return None
    try:
        return (int(p[0]) << 24) | (int(p[1]) << 16) | (int(p[2]) << 8) | int(p[3])
    except ValueError:
        return None

def _is_tailnet(addr):
    n = _ip2int(addr)
    return n is not None and TAILNET_LO <= n <= TAILNET_HI

DQ = chr(34)
DQ_PAT = re.compile(DQ + '([^' + DQ + ']+)' + DQ)

violations = []
sshd_public = False

for line in sys.stdin:
    line = line.strip()
    if not line.startswith('LISTEN'):
        continue
    parts = line.split()
    if len(parts) < 5:
        continue
    local = parts[3]

    if local.startswith('['):
        m = re.match(r'\[([^\]]+)\]:(\d+)', local)
        if not m:
            continue
        addr, port = m.group(1), m.group(2)
    else:
        idx = local.rfind(':')
        if idx < 0:
            continue
        addr, port = local[:idx], local[idx+1:]

    if addr in ('127.0.0.1', '::1'):
        continue

    pm = DQ_PAT.search(line)
    proc = pm.group(1) if pm else 'unknown'

    if _is_tailnet(addr):
        continue

    if proc in ('tailscaled', 'tailscale'):
        continue

    # Any remaining address is a violation (wildcard 0.0.0.0/:: or specific public IP)
    violations.append(proc + ' on ' + addr + ':' + port)
    if proc == 'sshd' and addr in ('0.0.0.0', '::', '*'):
        sshd_public = True

if violations:
    print('VIOLATIONS')
    for v in violations:
        print(v)
    if sshd_public:
        print('SSHD_PUBLIC')
else:
    print('OK')
" 2>/dev/null || echo "PARSE_ERROR")"

  if [ "$PORT_RESULT" = "OK" ]; then
    pass "No unexpected public port bindings (tailnet-aware policy)"
  elif echo "$PORT_RESULT" | head -1 | grep -q "VIOLATIONS"; then
    fail "UNEXPECTED PUBLIC PORT BINDINGS DETECTED:"
    echo "$PORT_RESULT" | grep -v '^VIOLATIONS$' | grep -v '^SSHD_PUBLIC$' | while IFS= read -r vline; do
      [ -n "$vline" ] && echo "    $vline" >&2
    done
    echo "" >&2
    echo "  Policy: services must bind to 127.0.0.1 or a Tailscale IP only." >&2
    echo "  Tailnet range 100.64.0.0/10 is treated as PRIVATE." >&2
    echo "  tailscaled listeners are always allowed." >&2

    # Remediation advice when sshd is bound to a public address
    if echo "$PORT_RESULT" | grep -q "SSHD_PUBLIC"; then
      echo "" >&2
      echo "  --- REMEDIATION: sshd is bound to a public address (0.0.0.0 / :::) ---" >&2
      echo "" >&2
      echo "  Run the automated fix (as root on the VPS):" >&2
      echo "    sudo ./ops/openclaw_fix_ssh_tailscale_only.sh" >&2
      echo "" >&2
      echo "  This will:" >&2
      echo "    1. Detect your Tailscale IPv4 address" >&2
      echo "    2. Write /etc/ssh/sshd_config.d/99-tailscale-only.conf" >&2
      echo "       (AddressFamily inet, ListenAddress <TAILSCALE_IP>)" >&2
      echo "    3. Validate config with: sshd -t" >&2
      echo "    4. Restart sshd: systemctl restart ssh" >&2
      echo "    5. Verify sshd is no longer on 0.0.0.0 / :::" >&2
      echo "" >&2
      echo "  After running the fix, re-run this doctor to confirm PASS." >&2
    fi
  elif [ "$PORT_RESULT" = "PARSE_ERROR" ]; then
    fail "Port audit parse error (check Python3 availability)"
  else
    fail "Port audit returned unexpected result: $PORT_RESULT"
  fi
elif command -v netstat >/dev/null 2>&1; then
  # macOS fallback — simplified check (no tailnet-aware parsing)
  PUBLIC_BINDS="$(netstat -an -p tcp 2>/dev/null | grep LISTEN | grep -E '(\*\.|0\.0\.0\.0)' || true)"
  if [ -n "$PUBLIC_BINDS" ]; then
    fail "UNEXPECTED PUBLIC PORT BINDINGS DETECTED (use Linux ss for tailnet-aware checks):"
    echo "$PUBLIC_BINDS" >&2
  else
    pass "No unexpected public port bindings"
  fi
else
  fail "Neither ss nor netstat available for port audit"
fi

# --- Summary ---
echo ""
echo "=== Doctor Summary: $((CHECKS - FAILURES))/$CHECKS passed ==="
if [ "$FAILURES" -gt 0 ]; then
  echo "FAIL: $FAILURES check(s) failed. See above for details." >&2
  exit 1
fi
echo "All checks passed."
exit 0

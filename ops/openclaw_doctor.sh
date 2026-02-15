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
#   1. 127.0.0.0/8 / ::1       → always allowed (loopback — includes systemd-resolve etc.)
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

def _is_loopback(addr):
    if addr == '::1':
        return True
    p = addr.split('.')
    if len(p) == 4:
        try:
            return int(p[0]) == 127
        except ValueError:
            return False
    return False

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

    if _is_loopback(addr):
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
      echo "    2. Disable ALL ssh socket-activation units (ssh.socket, sshd.socket, ssh@*)" >&2
      echo "    3. Scan and comment out conflicting ListenAddress/AddressFamily directives" >&2
      echo "    4. Write /etc/ssh/sshd_config.d/99-tailscale-only.conf" >&2
      echo "       (AddressFamily inet, ListenAddress <TAILSCALE_IP>)" >&2
      echo "    5. Validate with: sshd -t" >&2
      echo "    6. Restart the detected sshd service (ssh.service or sshd.service)" >&2
      echo "    7. Verify no public bindings remain; rollback on failure" >&2
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

# --- 5. Docker Published Ports Audit ---
echo "--- Docker Published Ports ---"
if command -v docker >/dev/null 2>&1; then
  DOCKER_PORT_RESULT="$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | python3 -c "
import sys, re

violations = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    # Parse port mappings like '127.0.0.1:8000->8000/tcp' or '0.0.0.0:5432->5432/tcp'
    binds = re.findall(r'([\d.]+):(\d+)->(\d+)/\w+', line)
    name = line.split()[0] if line.split() else 'unknown'
    for host_ip, host_port, container_port in binds:
        parts = host_ip.split('.')
        if len(parts) == 4:
            try:
                first_octet = int(parts[0])
            except ValueError:
                violations.append(f'{name}: {host_ip}:{host_port}')
                continue
            if first_octet == 127:
                continue  # loopback OK
            n = (int(parts[0]) << 24) | (int(parts[1]) << 16) | (int(parts[2]) << 8) | int(parts[3])
            lo = (100 << 24) | (64 << 16)
            hi = (100 << 24) | (127 << 16) | (255 << 8) | 255
            if lo <= n <= hi:
                continue  # tailnet OK
        violations.append(f'{name}: {host_ip}:{host_port}')

if violations:
    print('VIOLATIONS')
    for v in violations:
        print(v)
else:
    print('OK')
" 2>/dev/null || echo "PARSE_ERROR")"

  if [ "$DOCKER_PORT_RESULT" = "OK" ]; then
    pass "Docker published ports: all within allowed CIDRs"
  elif echo "$DOCKER_PORT_RESULT" | head -1 | grep -q "VIOLATIONS"; then
    fail "Docker containers with public port binds:"
    echo "$DOCKER_PORT_RESULT" | grep -v '^VIOLATIONS$' | while IFS= read -r vline; do
      [ -n "$vline" ] && echo "    $vline" >&2
    done
  elif [ "$DOCKER_PORT_RESULT" = "PARSE_ERROR" ]; then
    fail "Docker port audit parse error"
  else
    pass "No Docker containers running (or no published ports)"
  fi
else
  pass "Docker not installed (port audit N/A)"
fi

# --- 6. Disk Pressure + Log Growth ---
echo "--- Disk & Log Health ---"
DISK_WARN_PCT="${OPENCLAW_DOCTOR_DISK_WARN:-85}"
DISK_FAIL_PCT="${OPENCLAW_DOCTOR_DISK_FAIL:-95}"
LOG_SIZE_WARN_MB="${OPENCLAW_DOCTOR_LOG_WARN_MB:-500}"
LOG_SIZE_FAIL_MB="${OPENCLAW_DOCTOR_LOG_FAIL_MB:-2000}"

if command -v df >/dev/null 2>&1; then
  ROOT_USE="$(df / 2>/dev/null | tail -1 | awk '{print $5}' | tr -d '%' || echo "0")"
  if [ "$ROOT_USE" -ge "$DISK_FAIL_PCT" ] 2>/dev/null; then
    fail "Disk usage CRITICAL: ${ROOT_USE}% (threshold: ${DISK_FAIL_PCT}%)"
  elif [ "$ROOT_USE" -ge "$DISK_WARN_PCT" ] 2>/dev/null; then
    echo "  WARN: Disk usage at ${ROOT_USE}% (warn threshold: ${DISK_WARN_PCT}%)"
    pass "Disk usage WARNING but below critical (${ROOT_USE}%)"
  else
    pass "Disk usage OK (${ROOT_USE}%)"
  fi
else
  pass "df not available (disk check skipped)"
fi

# Check log directory sizes
if [ -d "$ROOT_DIR/logs" ]; then
  LOG_SIZE_KB="$(du -sk "$ROOT_DIR/logs" 2>/dev/null | cut -f1 || echo "0")"
  LOG_SIZE_MB=$((LOG_SIZE_KB / 1024))
  if [ "$LOG_SIZE_MB" -ge "$LOG_SIZE_FAIL_MB" ] 2>/dev/null; then
    fail "Log directory CRITICAL: ${LOG_SIZE_MB}MB (threshold: ${LOG_SIZE_FAIL_MB}MB)"
  elif [ "$LOG_SIZE_MB" -ge "$LOG_SIZE_WARN_MB" ] 2>/dev/null; then
    echo "  WARN: Log directory at ${LOG_SIZE_MB}MB (warn: ${LOG_SIZE_WARN_MB}MB)"
    pass "Log directory WARNING but below critical (${LOG_SIZE_MB}MB)"
  else
    pass "Log directory OK (${LOG_SIZE_MB}MB)"
  fi
else
  pass "No logs directory (log check N/A)"
fi

# --- 7. Key Health (presence + last success; no network by default) ---
echo "--- Key Health ---"
SMOKE_MODE="${OPENCLAW_DOCTOR_SMOKE:-0}"

# OpenAI key presence
OPENAI_STATUS=""
if [ -f "$SCRIPT_DIR/openai_key.py" ]; then
  OPENAI_STATUS="$(python3 "$SCRIPT_DIR/openai_key.py" status 2>/dev/null || echo "not available")"
  OPENAI_FINGERPRINT="$(echo "$OPENAI_STATUS" | head -1 | grep -o 'sk-\.\.\.[a-zA-Z0-9]*' || echo "unknown")"
  if echo "$OPENAI_STATUS" | grep -q "sk-"; then
    pass "OpenAI API key present (fingerprint: $OPENAI_FINGERPRINT)"

    # Smoke mode: actually test the key (requires network)
    if [ "$SMOKE_MODE" = "1" ]; then
      echo "  [smoke] Testing OpenAI API connectivity..."
      SMOKE_RC=0
      python3 "$SCRIPT_DIR/openai_key.py" doctor 2>/dev/null || SMOKE_RC=$?
      if [ "$SMOKE_RC" -eq 0 ]; then
        pass "OpenAI API smoke test PASS"
      else
        fail "OpenAI API smoke test FAIL (rc=$SMOKE_RC)"
      fi
    fi
  else
    fail "OpenAI API key not configured"
  fi
else
  fail "openai_key.py not found"
fi

# Pushover key presence (optional — warn only)
PUSHOVER_APP=""
if [ -n "${PUSHOVER_APP_TOKEN:-}" ]; then
  PUSHOVER_APP="env"
elif command -v security >/dev/null 2>&1; then
  PUSHOVER_APP="$(security find-generic-password -a PUSHOVER_APP_TOKEN -s ai-ops-runner -w 2>/dev/null || true)"
  [ -n "$PUSHOVER_APP" ] && PUSHOVER_APP="keychain"
elif [ -f /etc/ai-ops-runner/secrets/pushover_app_token ]; then
  PUSHOVER_APP="file"
fi

if [ -n "$PUSHOVER_APP" ]; then
  pass "Pushover app token present (source: $PUSHOVER_APP)"
else
  echo "  WARN: Pushover app token not configured (notifications disabled)"
  pass "Pushover token absent (optional; notifications disabled)"
fi

# --- 8. Console Bind Check ---
echo "--- Console Bind Check ---"
CONSOLE_PORT="${OPENCLAW_CONSOLE_PORT:-8787}"
if command -v ss >/dev/null 2>&1; then
  CONSOLE_BIND="$(ss -tlnp 2>/dev/null | grep ":${CONSOLE_PORT} " || true)"
  if [ -z "$CONSOLE_BIND" ]; then
    pass "Console not running (or port ${CONSOLE_PORT} not bound)"
  elif echo "$CONSOLE_BIND" | grep -q "127.0.0.1:${CONSOLE_PORT}"; then
    pass "Console bound to 127.0.0.1:${CONSOLE_PORT} (private-only)"
  elif echo "$CONSOLE_BIND" | grep -qE "0\.0\.0\.0:${CONSOLE_PORT}|\[::\]:${CONSOLE_PORT}|\*:${CONSOLE_PORT}"; then
    fail "Console bound to public address! Must be 127.0.0.1 only."
  else
    pass "Console bind appears private"
  fi
elif command -v netstat >/dev/null 2>&1; then
  CONSOLE_BIND="$(netstat -an -p tcp 2>/dev/null | grep ":${CONSOLE_PORT} " | grep LISTEN || true)"
  if [ -z "$CONSOLE_BIND" ]; then
    pass "Console not running (or port ${CONSOLE_PORT} not bound)"
  elif echo "$CONSOLE_BIND" | grep -q '127.0.0.1'; then
    pass "Console bound to 127.0.0.1:${CONSOLE_PORT} (private-only)"
  else
    fail "Console may be bound to a public address — verify manually"
  fi
else
  pass "Port check unavailable (console bind check skipped)"
fi

# --- 9. Guard Timer Health ---
echo "--- Guard Timer Health ---"
if command -v systemctl >/dev/null 2>&1; then
  GUARD_TIMER_ACTIVE="$(systemctl is-active openclaw-guard.timer 2>/dev/null || echo "inactive")"
  if [ "$GUARD_TIMER_ACTIVE" = "active" ]; then
    pass "Guard timer active"
    # Check for recent PASS/FAIL in guard log
    if [ -f /var/log/openclaw_guard.log ]; then
      LAST_ENTRY="$(tail -20 /var/log/openclaw_guard.log 2>/dev/null | grep -E 'RESULT: (PASS|FAIL)' | tail -1 || true)"
      if [ -n "$LAST_ENTRY" ]; then
        if echo "$LAST_ENTRY" | grep -q "RESULT: PASS"; then
          pass "Guard last result: PASS"
        else
          echo "  WARN: Guard last result: FAIL — check /var/log/openclaw_guard.log"
          pass "Guard timer running (last result: FAIL — monitoring)"
        fi
      else
        pass "Guard timer running (no results yet)"
      fi
    else
      pass "Guard timer running (no log file yet)"
    fi
  else
    fail "Guard timer not active (state: $GUARD_TIMER_ACTIVE)"
  fi
else
  # macOS / non-systemd — skip
  pass "Guard timer check N/A (no systemd)"
fi

# --- JSON Output ---
DOCTOR_TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
DOCTOR_JSON_DIR="$ROOT_DIR/artifacts/doctor/${DOCTOR_TIMESTAMP}"
mkdir -p "$DOCTOR_JSON_DIR" 2>/dev/null || true

python3 - "$DOCTOR_JSON_DIR/doctor.json" "$CHECKS" "$FAILURES" "$(hostname 2>/dev/null || echo unknown)" <<'PYEOF'
import json, sys
from datetime import datetime, timezone
out_file = sys.argv[1]
checks = int(sys.argv[2])
failures = int(sys.argv[3])
hostname = sys.argv[4]
result = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "hostname": hostname,
    "result": "PASS" if failures == 0 else "FAIL",
    "checks_total": checks,
    "checks_passed": checks - failures,
    "checks_failed": failures
}
with open(out_file, "w") as f:
    json.dump(result, f, indent=2)
PYEOF

# --- Summary ---
echo ""
echo "=== Doctor Summary: $((CHECKS - FAILURES))/$CHECKS passed ==="
echo "  JSON: $DOCTOR_JSON_DIR/doctor.json"
if [ "$FAILURES" -gt 0 ]; then
  echo "FAIL: $FAILURES check(s) failed. See above for details." >&2
  # Send notification on failure (if available)
  if [ -x "$SCRIPT_DIR/openclaw_notify.sh" ] && [ "${OPENCLAW_DOCTOR_NOTIFY:-0}" = "1" ]; then
    "$SCRIPT_DIR/openclaw_notify.sh" \
      --priority high \
      --title "OpenClaw Doctor" \
      --rate-key "doctor_fail" \
      "[$(hostname 2>/dev/null || echo unknown)] $FAILURES check(s) failed — run ./ops/openclaw_doctor.sh for details" 2>/dev/null || true
  fi
  # SMS notification on failure (if configured)
  if [ -x "$SCRIPT_DIR/openclaw_notify_sms.sh" ] && [ "${OPENCLAW_DOCTOR_NOTIFY:-0}" = "1" ]; then
    "$SCRIPT_DIR/openclaw_notify_sms.sh" \
      --event "DOCTOR_FAIL" \
      --message "[$(hostname 2>/dev/null || echo unknown)] $FAILURES check(s) failed" \
      --rate-key "doctor_fail" 2>/dev/null || true
  fi
  exit 1
fi
echo "All checks passed."
exit 0

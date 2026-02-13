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

# --- 4. No public port bindings ---
echo "--- Public Port Audit ---"
# Check for any TCP LISTEN sockets bound to 0.0.0.0 or [::]
# These would be accessible from outside the machine (violates no-public-ports policy).
if command -v ss >/dev/null 2>&1; then
  # ss -tlnp: TCP, listening, numeric, show process
  PUBLIC_BINDS="$(ss -tlnp 2>/dev/null | grep -E '(0\.0\.0\.0|::):' | grep -v '127\.' | grep -v '::1' || true)"
  if [ -n "$PUBLIC_BINDS" ]; then
    fail "UNEXPECTED PUBLIC PORT BINDINGS DETECTED:"
    echo "$PUBLIC_BINDS" >&2
    echo "" >&2
    echo "  All services must bind to 127.0.0.1 only (no-public-ports policy)." >&2
  else
    pass "No unexpected public port bindings"
  fi
elif command -v netstat >/dev/null 2>&1; then
  # Fallback for macOS (no ss)
  PUBLIC_BINDS="$(netstat -an -p tcp 2>/dev/null | grep LISTEN | grep -E '(\*\.|0\.0\.0\.0)' || true)"
  if [ -n "$PUBLIC_BINDS" ]; then
    fail "UNEXPECTED PUBLIC PORT BINDINGS DETECTED:"
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

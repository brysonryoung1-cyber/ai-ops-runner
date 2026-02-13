#!/usr/bin/env bash
# openclaw_doctor_selftest.sh — Test the tailnet-aware port audit parsing logic.
# Hermetic: no real Tailscale, UFW, ss, or network dependencies.
# Feeds mock ss -tlnp output to the same Python analyzer used by openclaw_doctor.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

ERRORS=0
PASS=0

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1" >&2; ERRORS=$((ERRORS + 1)); }

echo "=== openclaw_doctor Port Audit Selftest ==="
echo ""

# ---------------------------------------------------------------------------
# Python port-audit analyzer (same logic as openclaw_doctor.sh section 4)
# ---------------------------------------------------------------------------
# Stored in a file to avoid quoting issues when piping mock data.
ANALYZER="$(mktemp)"
trap 'rm -f "$ANALYZER"' EXIT

cat > "$ANALYZER" << 'PYEOF'
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
PYEOF

run_analyzer() {
  python3 "$ANALYZER" <<< "$1"
}

# ---------------------------------------------------------------------------
# Test 1: sshd on 0.0.0.0:22 → FAIL + SSHD_PUBLIC
# ---------------------------------------------------------------------------
echo "--- Test 1: sshd on 0.0.0.0 → FAIL ---"
RESULT="$(run_analyzer 'LISTEN  0  128  0.0.0.0:22  0.0.0.0:*  users:(("sshd",pid=123,fd=3))')"
if echo "$RESULT" | grep -q "VIOLATIONS" && echo "$RESULT" | grep -q "SSHD_PUBLIC"; then
  pass "sshd on 0.0.0.0:22 detected as violation + SSHD_PUBLIC"
else
  fail "sshd on 0.0.0.0:22 not properly detected: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 2: sshd on tailnet IP → PASS
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 2: sshd on tailnet IP → PASS ---"
RESULT="$(run_analyzer 'LISTEN  0  128  100.123.61.57:22  0.0.0.0:*  users:(("sshd",pid=123,fd=3))')"
if [ "$RESULT" = "OK" ]; then
  pass "sshd on tailnet IP (100.123.61.57:22) treated as PRIVATE"
else
  fail "sshd on tailnet IP should be OK, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 3: tailscaled on tailnet IP → PASS
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 3: tailscaled on tailnet IP → PASS ---"
RESULT="$(run_analyzer 'LISTEN  0  4096  100.123.61.57:40469  0.0.0.0:*  users:(("tailscaled",pid=456,fd=9))')"
if [ "$RESULT" = "OK" ]; then
  pass "tailscaled on tailnet IP treated as PRIVATE"
else
  fail "tailscaled on tailnet IP should be OK, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 4: tailscaled on 0.0.0.0 → PASS (allowed for DERP relay)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 4: tailscaled on 0.0.0.0 → PASS ---"
RESULT="$(run_analyzer 'LISTEN  0  4096  0.0.0.0:41641  0.0.0.0:*  users:(("tailscaled",pid=456,fd=12))')"
if [ "$RESULT" = "OK" ]; then
  pass "tailscaled on 0.0.0.0:41641 allowed (DERP relay)"
else
  fail "tailscaled on 0.0.0.0 should be OK, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 5: localhost bind → PASS
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 5: localhost bind → PASS ---"
RESULT="$(run_analyzer 'LISTEN  0  128  127.0.0.1:8000  0.0.0.0:*  users:(("uvicorn",pid=789,fd=5))')"
if [ "$RESULT" = "OK" ]; then
  pass "127.0.0.1:8000 treated as localhost (OK)"
else
  fail "127.0.0.1 should be OK, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 6: unknown process on 0.0.0.0 → FAIL
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 6: nginx on 0.0.0.0 → FAIL ---"
RESULT="$(run_analyzer 'LISTEN  0  128  0.0.0.0:9999  0.0.0.0:*  users:(("nginx",pid=999,fd=7))')"
if echo "$RESULT" | grep -q "VIOLATIONS" && echo "$RESULT" | grep -q "nginx"; then
  pass "nginx on 0.0.0.0:9999 detected as violation"
else
  fail "nginx on 0.0.0.0:9999 not detected: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 7: Mixed scenario — only sshd on 0.0.0.0 should fail
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 7: Mixed scenario (sshd public + tailscaled tailnet + localhost) ---"
MOCK_SS="LISTEN  0  128  0.0.0.0:22  0.0.0.0:*  users:((\"sshd\",pid=123,fd=3))
LISTEN  0  4096  100.123.61.57:40469  0.0.0.0:*  users:((\"tailscaled\",pid=456,fd=9))
LISTEN  0  4096  127.0.0.1:8000  0.0.0.0:*  users:((\"uvicorn\",pid=789,fd=5))"
RESULT="$(run_analyzer "$MOCK_SS")"
if echo "$RESULT" | grep -q "VIOLATIONS" && echo "$RESULT" | grep -q "sshd" && echo "$RESULT" | grep -q "SSHD_PUBLIC"; then
  VCOUNT="$(echo "$RESULT" | grep -c ' on ')"
  if [ "$VCOUNT" -eq 1 ]; then
    pass "Mixed: only sshd violation (tailscaled + localhost OK)"
  else
    fail "Mixed: expected 1 violation, got $VCOUNT: $RESULT"
  fi
else
  fail "Mixed scenario failed: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 8: All clean (sshd on tailnet, tailscaled on tailnet, uvicorn on localhost)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 8: All clean ---"
MOCK_SS="LISTEN  0  128  100.64.0.1:22  0.0.0.0:*  users:((\"sshd\",pid=123,fd=3))
LISTEN  0  4096  100.123.61.57:40469  0.0.0.0:*  users:((\"tailscaled\",pid=456,fd=9))
LISTEN  0  4096  127.0.0.1:8000  0.0.0.0:*  users:((\"uvicorn\",pid=789,fd=5))"
RESULT="$(run_analyzer "$MOCK_SS")"
if [ "$RESULT" = "OK" ]; then
  pass "All clean: sshd tailnet + tailscaled tailnet + uvicorn localhost"
else
  fail "All clean should be OK, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 9: sshd on IPv6 wildcard [::] → FAIL
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 9: sshd on [::] → FAIL ---"
RESULT="$(run_analyzer 'LISTEN  0  128  [::]:22  [::]:*  users:(("sshd",pid=123,fd=4))')"
if echo "$RESULT" | grep -q "VIOLATIONS" && echo "$RESULT" | grep -q "SSHD_PUBLIC"; then
  pass "sshd on [::]:22 detected as violation + SSHD_PUBLIC"
else
  fail "sshd on [::]:22 not detected: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 10: Tailnet boundary — 100.63.255.255 is NOT tailnet
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 10: 100.63.255.255 is NOT tailnet ---"
RESULT="$(run_analyzer 'LISTEN  0  128  100.63.255.255:22  0.0.0.0:*  users:(("sshd",pid=123,fd=3))')"
if echo "$RESULT" | grep -q "VIOLATIONS"; then
  pass "100.63.255.255 correctly NOT in tailnet range"
else
  fail "100.63.255.255 should NOT be tailnet, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 11: Tailnet boundary — 100.64.0.0 IS tailnet
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 11: 100.64.0.0 IS tailnet ---"
RESULT="$(run_analyzer 'LISTEN  0  128  100.64.0.0:22  0.0.0.0:*  users:(("sshd",pid=123,fd=3))')"
if [ "$RESULT" = "OK" ]; then
  pass "100.64.0.0 correctly in tailnet range"
else
  fail "100.64.0.0 should be tailnet, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 12: Tailnet boundary — 100.127.255.255 IS tailnet (upper bound)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 12: 100.127.255.255 IS tailnet (upper bound) ---"
RESULT="$(run_analyzer 'LISTEN  0  128  100.127.255.255:22  0.0.0.0:*  users:(("sshd",pid=123,fd=3))')"
if [ "$RESULT" = "OK" ]; then
  pass "100.127.255.255 correctly in tailnet range"
else
  fail "100.127.255.255 should be tailnet, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 13: 100.128.0.0 is NOT tailnet (just above range)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 13: 100.128.0.0 is NOT tailnet ---"
RESULT="$(run_analyzer 'LISTEN  0  128  100.128.0.0:22  0.0.0.0:*  users:(("sshd",pid=123,fd=3))')"
if echo "$RESULT" | grep -q "VIOLATIONS"; then
  pass "100.128.0.0 correctly NOT in tailnet range"
else
  fail "100.128.0.0 should NOT be tailnet, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 14: IPv6 localhost [::1] → PASS
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 14: [::1] localhost → PASS ---"
RESULT="$(run_analyzer 'LISTEN  0  128  [::1]:5432  [::]:*  users:(("postgres",pid=500,fd=6))')"
if [ "$RESULT" = "OK" ]; then
  pass "[::1]:5432 treated as localhost (OK)"
else
  fail "[::1] should be OK, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 15: No LISTEN lines → PASS (empty)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 15: No LISTEN lines → PASS ---"
RESULT="$(run_analyzer 'State  Recv-Q  Send-Q  Local  Peer  Process')"
if [ "$RESULT" = "OK" ]; then
  pass "No LISTEN lines → OK"
else
  fail "Empty should be OK, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 16: systemd-resolve on 127.0.0.53:53 → PASS (loopback)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 16: systemd-resolve on 127.0.0.53:53 → PASS ---"
RESULT="$(run_analyzer 'LISTEN  0  4096  127.0.0.53:53  0.0.0.0:*  users:(("systemd-resolve",pid=400,fd=17))')"
if [ "$RESULT" = "OK" ]; then
  pass "127.0.0.53:53 (systemd-resolve) treated as loopback (OK)"
else
  fail "127.0.0.53 should be loopback, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 17: systemd-resolve on 127.0.0.54:53 → PASS (loopback)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 17: systemd-resolve on 127.0.0.54:53 → PASS ---"
RESULT="$(run_analyzer 'LISTEN  0  4096  127.0.0.54:53  0.0.0.0:*  users:(("systemd-resolve",pid=400,fd=18))')"
if [ "$RESULT" = "OK" ]; then
  pass "127.0.0.54:53 (systemd-resolve) treated as loopback (OK)"
else
  fail "127.0.0.54 should be loopback, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 18: Full VPS scenario — sshd tailnet + systemd-resolve + tailscaled
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 18: Full VPS scenario (clean) ---"
MOCK_SS="LISTEN  0  128  100.123.61.57:22  0.0.0.0:*  users:((\"sshd\",pid=123,fd=3))
LISTEN  0  4096  100.123.61.57:40469  0.0.0.0:*  users:((\"tailscaled\",pid=456,fd=9))
LISTEN  0  4096  127.0.0.1:8000  0.0.0.0:*  users:((\"uvicorn\",pid=789,fd=5))
LISTEN  0  4096  127.0.0.53:53  0.0.0.0:*  users:((\"systemd-resolve\",pid=400,fd=17))
LISTEN  0  4096  127.0.0.54:53  0.0.0.0:*  users:((\"systemd-resolve\",pid=400,fd=18))"
RESULT="$(run_analyzer "$MOCK_SS")"
if [ "$RESULT" = "OK" ]; then
  pass "Full VPS scenario: all private (sshd tailnet + resolve loopback + tailscaled)"
else
  fail "Full VPS scenario should be OK, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 19: sshd public + systemd-resolve → only sshd FAIL
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 19: sshd public + systemd-resolve → only sshd FAIL ---"
MOCK_SS="LISTEN  0  128  0.0.0.0:22  0.0.0.0:*  users:((\"sshd\",pid=123,fd=3))
LISTEN  0  4096  127.0.0.53:53  0.0.0.0:*  users:((\"systemd-resolve\",pid=400,fd=17))
LISTEN  0  4096  127.0.0.54:53  0.0.0.0:*  users:((\"systemd-resolve\",pid=400,fd=18))
LISTEN  0  4096  127.0.0.1:8000  0.0.0.0:*  users:((\"uvicorn\",pid=789,fd=5))"
RESULT="$(run_analyzer "$MOCK_SS")"
if echo "$RESULT" | grep -q "VIOLATIONS" && echo "$RESULT" | grep -q "SSHD_PUBLIC"; then
  VCOUNT="$(echo "$RESULT" | grep -c ' on ')"
  if [ "$VCOUNT" -eq 1 ]; then
    pass "sshd public + systemd-resolve: only sshd violation (resolve OK)"
  else
    fail "Expected 1 violation, got $VCOUNT: $RESULT"
  fi
else
  fail "sshd public + systemd-resolve: $RESULT"
fi

# ---------------------------------------------------------------------------
# Test 20: 127.99.99.99 is loopback (full 127.0.0.0/8 coverage)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 20: 127.99.99.99 is loopback → PASS ---"
RESULT="$(run_analyzer 'LISTEN  0  128  127.99.99.99:9999  0.0.0.0:*  users:(("someproc",pid=555,fd=3))')"
if [ "$RESULT" = "OK" ]; then
  pass "127.99.99.99 treated as loopback (full 127.0.0.0/8 coverage)"
else
  fail "127.99.99.99 should be loopback, got: $RESULT"
fi

# ---------------------------------------------------------------------------
# Verify openclaw_doctor.sh contains the tailnet-aware code
# ---------------------------------------------------------------------------
echo ""
echo "--- Static checks ---"
DOCTOR="$ROOT_DIR/ops/openclaw_doctor.sh"
if grep -q '_is_tailnet' "$DOCTOR"; then
  pass "openclaw_doctor.sh contains _is_tailnet function"
else
  fail "openclaw_doctor.sh missing _is_tailnet function"
fi
if grep -q '_is_loopback' "$DOCTOR"; then
  pass "openclaw_doctor.sh contains _is_loopback function"
else
  fail "openclaw_doctor.sh missing _is_loopback function"
fi
if grep -q 'ssh\.socket' "$DOCTOR"; then
  pass "openclaw_doctor.sh references ssh.socket in remediation"
else
  fail "openclaw_doctor.sh missing ssh.socket reference in remediation"
fi
if grep -q 'sshd\.socket' "$DOCTOR"; then
  pass "openclaw_doctor.sh references sshd.socket in remediation"
else
  fail "openclaw_doctor.sh missing sshd.socket reference in remediation"
fi
if grep -q 'ssh@' "$DOCTOR"; then
  pass "openclaw_doctor.sh references ssh@* in remediation"
else
  fail "openclaw_doctor.sh missing ssh@* reference in remediation"
fi
if grep -q 'rollback' "$DOCTOR"; then
  pass "openclaw_doctor.sh mentions rollback in remediation"
else
  fail "openclaw_doctor.sh missing rollback mention in remediation"
fi
if grep -q 'SSHD_PUBLIC' "$DOCTOR"; then
  pass "openclaw_doctor.sh detects SSHD_PUBLIC"
else
  fail "openclaw_doctor.sh missing SSHD_PUBLIC detection"
fi
if grep -q 'openclaw_fix_ssh_tailscale_only' "$DOCTOR"; then
  pass "openclaw_doctor.sh references remediation script"
else
  fail "openclaw_doctor.sh missing remediation script reference"
fi
if [ -f "$ROOT_DIR/ops/openclaw_fix_ssh_tailscale_only.sh" ]; then
  pass "ops/openclaw_fix_ssh_tailscale_only.sh exists"
  if [ -x "$ROOT_DIR/ops/openclaw_fix_ssh_tailscale_only.sh" ]; then
    pass "ops/openclaw_fix_ssh_tailscale_only.sh is executable"
  else
    fail "ops/openclaw_fix_ssh_tailscale_only.sh NOT executable"
  fi
else
  fail "ops/openclaw_fix_ssh_tailscale_only.sh missing"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Summary: $PASS passed, $ERRORS failed ==="
if [ "$ERRORS" -gt 0 ]; then
  echo "  $ERRORS error(s) found." >&2
  exit 1
fi
echo "  All port audit tests passed!"
exit 0

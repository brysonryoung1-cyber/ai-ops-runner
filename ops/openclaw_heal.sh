#!/usr/bin/env bash
# openclaw_heal.sh — One-command Apply + Verify + Evidence entrypoint
#
# Idempotent. Fail-closed. Produces timestamped evidence bundle.
#
# Usage:
#   sudo ./ops/openclaw_heal.sh                   # Full heal cycle
#   sudo ./ops/openclaw_heal.sh --check-only      # Pre-check only
#   sudo ./ops/openclaw_heal.sh --verify-only     # Doctor + evidence only
#   sudo ./ops/openclaw_heal.sh --notify          # Send Pushover on result
#
# Exit codes:
#   0 = heal PASS (evidence written)
#   1 = heal FAIL (check or doctor failed)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# --- Defaults ---
CHECK_ONLY=0
VERIFY_ONLY=0
NOTIFY=0
HOSTNAME_VAL="$(hostname 2>/dev/null || echo "unknown")"
TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
EVIDENCE_DIR="$ROOT_DIR/artifacts/evidence/${TIMESTAMP}_${HOSTNAME_VAL}"

# For testing: allow overrides
OPENCLAW_HEAL_TEST_MODE="${OPENCLAW_HEAL_TEST_MODE:-0}"
OPENCLAW_HEAL_TEST_ROOT="${OPENCLAW_HEAL_TEST_ROOT:-}"

# --- Parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only)  CHECK_ONLY=1; shift ;;
    --verify-only) VERIFY_ONLY=1; shift ;;
    --notify)      NOTIFY=1; shift ;;
    -h|--help)
      echo "Usage: openclaw_heal.sh [--check-only] [--verify-only] [--notify]"
      exit 0
      ;;
    *) echo "ERROR: Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# --- Helpers ---
log() { echo "[heal] $1"; }
fail_exit() {
  log "HEAL FAIL: $1"
  if [ "$NOTIFY" -eq 1 ]; then
    "$SCRIPT_DIR/openclaw_notify.sh" --priority high --title "OpenClaw Heal" \
      "FAIL on $HOSTNAME_VAL: $1" 2>/dev/null || true
  fi
  exit 1
}

echo "=== openclaw_heal.sh ==="
echo "  Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  Host: $HOSTNAME_VAL"
echo "  Evidence: $EVIDENCE_DIR"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Private-Only Posture Pre-Check
# ─────────────────────────────────────────────────────────────────────────────
log "Step 1: Private-only posture pre-check"

PRECHECK_OUTPUT=""
PRECHECK_PASS=true

if command -v ss >/dev/null 2>&1; then
  LISTENERS="$(ss -tlnp 2>/dev/null || true)"
  PRECHECK_OUTPUT="$LISTENERS"

  # Check for public binds using the same Python analyzer as doctor
  POSTURE_CHECK="$(echo "$LISTENERS" | python3 -c "
import sys, re

TAILNET_LO = (100 << 24) | (64 << 16)
TAILNET_HI = (100 << 24) | (127 << 16) | (255 << 8) | 255

def _ip2int(ip):
    p = ip.split('.')
    if len(p) != 4: return None
    try: return (int(p[0]) << 24) | (int(p[1]) << 16) | (int(p[2]) << 8) | int(p[3])
    except ValueError: return None

def _is_tailnet(addr):
    n = _ip2int(addr)
    return n is not None and TAILNET_LO <= n <= TAILNET_HI

def _is_loopback(addr):
    if addr == '::1': return True
    p = addr.split('.')
    if len(p) == 4:
        try: return int(p[0]) == 127
        except ValueError: return False
    return False

violations = []
for line in sys.stdin:
    line = line.strip()
    if not line.startswith('LISTEN'): continue
    parts = line.split()
    if len(parts) < 5: continue
    local = parts[3]
    if local.startswith('['):
        m = re.match(r'\[([^\]]+)\]:(\d+)', local)
        if not m: continue
        addr = m.group(1)
    else:
        idx = local.rfind(':')
        if idx < 0: continue
        addr = local[:idx]
    if _is_loopback(addr) or _is_tailnet(addr): continue
    # Extract process name
    DQ = chr(34)
    pm = re.search(DQ + '([^' + DQ + ']+)' + DQ, line)
    proc = pm.group(1) if pm else 'unknown'
    if proc in ('tailscaled', 'tailscale'): continue
    violations.append(proc + ' on ' + addr)

if violations:
    print('VIOLATIONS: ' + '; '.join(violations))
else:
    print('OK')
" 2>/dev/null || echo "PARSE_ERROR")"

  if [ "$POSTURE_CHECK" = "OK" ]; then
    log "  Pre-check: PASS (all listeners private-only)"
  else
    log "  Pre-check: $POSTURE_CHECK"
    PRECHECK_PASS=false
    if [ "$CHECK_ONLY" -eq 1 ]; then
      fail_exit "Public listeners detected in pre-check: $POSTURE_CHECK"
    fi
  fi
else
  log "  Pre-check: SKIPPED (ss not available)"
  PRECHECK_OUTPUT="ss not available"
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  log "HEAL PASS (check-only): posture pre-check passed"
  exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Apply Hardened Fixes (Optional, Idempotent)
# ─────────────────────────────────────────────────────────────────────────────
FIXES_APPLIED=false
FIX_OUTPUT=""

if [ "$VERIFY_ONLY" -eq 0 ]; then
  log "Step 2: Applying hardened fixes (idempotent)"

  # Check Tailscale
  TS_IP=""
  if command -v tailscale >/dev/null 2>&1; then
    TS_IP="$(tailscale ip -4 2>/dev/null | head -n1 | tr -d '[:space:]' || true)"
  fi

  if [ -z "$TS_IP" ]; then
    log "  Tailscale IPv4 not available — skipping SSH fix (lockout prevention)"
  elif [ "$PRECHECK_PASS" = "false" ]; then
    log "  Tailscale up ($TS_IP), applying SSH fix..."
    FIX_RC=0
    if [ "$OPENCLAW_HEAL_TEST_MODE" = "1" ] && [ -n "$OPENCLAW_HEAL_TEST_ROOT" ]; then
      FIX_OUTPUT="$("$OPENCLAW_HEAL_TEST_ROOT/fix_stub.sh" 2>&1)" || FIX_RC=$?
    elif [ "$(id -u)" -eq 0 ]; then
      # Already root — run directly (no sudo needed)
      FIX_OUTPUT="$("$SCRIPT_DIR/openclaw_fix_ssh_tailscale_only.sh" 2>&1)" || FIX_RC=$?
    elif command -v sudo >/dev/null 2>&1; then
      FIX_OUTPUT="$(sudo "$SCRIPT_DIR/openclaw_fix_ssh_tailscale_only.sh" 2>&1)" || FIX_RC=$?
    else
      log "  WARNING: Not root and sudo not available — skipping SSH fix"
      FIX_RC=1
      FIX_OUTPUT="Not root, sudo not available"
    fi

    if [ "$FIX_RC" -eq 0 ]; then
      log "  SSH fix applied successfully"
      FIXES_APPLIED=true
    else
      log "  SSH fix FAILED (rc=$FIX_RC)"
    fi
  else
    log "  Posture already clean — no fixes needed"
  fi
else
  log "Step 2: SKIPPED (--verify-only)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Doctor Verification
# ─────────────────────────────────────────────────────────────────────────────
log "Step 3: Running openclaw_doctor.sh"

DOCTOR_RC=0
DOCTOR_OUTPUT=""
if [ "$OPENCLAW_HEAL_TEST_MODE" = "1" ] && [ -n "$OPENCLAW_HEAL_TEST_ROOT" ]; then
  DOCTOR_OUTPUT="$("$OPENCLAW_HEAL_TEST_ROOT/doctor_stub.sh" 2>&1)" || DOCTOR_RC=$?
else
  DOCTOR_OUTPUT="$("$SCRIPT_DIR/openclaw_doctor.sh" 2>&1)" || DOCTOR_RC=$?
fi

echo "$DOCTOR_OUTPUT"

if [ "$DOCTOR_RC" -ne 0 ]; then
  fail_exit "Doctor did not PASS (rc=$DOCTOR_RC)"
fi

log "  Doctor: PASS"

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Evidence Bundle
# ─────────────────────────────────────────────────────────────────────────────
log "Step 4: Capturing evidence bundle"

mkdir -p "$EVIDENCE_DIR"

# 4a. Current listeners snapshot
if command -v ss >/dev/null 2>&1; then
  ss -tlnp 2>/dev/null > "$EVIDENCE_DIR/listeners.txt" || echo "ss failed" > "$EVIDENCE_DIR/listeners.txt"
else
  echo "ss not available" > "$EVIDENCE_DIR/listeners.txt"
fi

# 4b. Effective sshd config summary (no secrets)
if command -v sshd >/dev/null 2>&1; then
  sshd -T 2>/dev/null | grep -iE '^(listenaddress|addressfamily|port|permitrootlogin|passwordauthentication|pubkeyauthentication)' \
    > "$EVIDENCE_DIR/sshd_config.txt" 2>/dev/null || echo "sshd -T not available" > "$EVIDENCE_DIR/sshd_config.txt"
else
  echo "sshd not available" > "$EVIDENCE_DIR/sshd_config.txt"
fi

# 4c. Guard timer status + last N log lines
{
  if command -v systemctl >/dev/null 2>&1; then
    systemctl status openclaw-guard.timer --no-pager 2>/dev/null || echo "guard timer not installed"
    echo "---"
    journalctl -u openclaw-guard.service -n 50 --no-pager 2>/dev/null || echo "no guard journal"
  else
    echo "systemctl not available"
  fi
  if [ -f /var/log/openclaw_guard.log ]; then
    echo "--- last 50 guard log lines ---"
    tail -50 /var/log/openclaw_guard.log 2>/dev/null || true
  fi
} > "$EVIDENCE_DIR/guard_status.txt" 2>&1

# 4d. Docker published ports summary
{
  if command -v docker >/dev/null 2>&1; then
    docker compose ps --format "table {{.Name}}\t{{.Ports}}\t{{.State}}" 2>/dev/null || echo "docker compose not running"
    echo "---"
    docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}" 2>/dev/null || echo "no containers"
  else
    echo "docker not available"
  fi
} > "$EVIDENCE_DIR/docker_ports.txt" 2>&1

# 4e. Doctor output
echo "$DOCTOR_OUTPUT" > "$EVIDENCE_DIR/doctor_output.txt"

# 4f. Summary JSON
python3 - "$EVIDENCE_DIR/summary.json" "$TIMESTAMP" "$HOSTNAME_VAL" "$DOCTOR_RC" "$FIXES_APPLIED" "$EVIDENCE_DIR" "$PRECHECK_PASS" <<'PYEOF'
import json, sys
out_file = sys.argv[1]
summary = {
    "timestamp": sys.argv[2],
    "hostname": sys.argv[3],
    "result": "PASS" if int(sys.argv[4]) == 0 else "FAIL",
    "doctor_rc": int(sys.argv[4]),
    "checks": {
        "private_posture": "PASS" if sys.argv[7] == "true" else "FAIL",
        "fixes_applied": sys.argv[5] == "true",
        "doctor_pass": int(sys.argv[4]) == 0
    },
    "artifact_path": sys.argv[6]
}
with open(out_file, "w") as f:
    json.dump(summary, f, indent=2)
PYEOF

# 4g. README
cat > "$EVIDENCE_DIR/README.txt" <<EOF
OpenClaw Heal Evidence Bundle
Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
Host: $HOSTNAME_VAL

This directory contains evidence from an openclaw_heal.sh run.

Files:
  listeners.txt    — TCP listening ports at time of capture
  sshd_config.txt  — Effective sshd configuration (security-relevant fields only)
  guard_status.txt — Guard timer status + recent log entries
  docker_ports.txt — Docker container ports and status
  doctor_output.txt — Full doctor output
  summary.json     — Machine-readable summary

No secrets are included in this bundle.
EOF

log "  Evidence written to: $EVIDENCE_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Summary
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  HEAL PASS                                          ║"
echo "║  Evidence: $EVIDENCE_DIR"
echo "╚══════════════════════════════════════════════════════╝"

if [ "$NOTIFY" -eq 1 ]; then
  "$SCRIPT_DIR/openclaw_notify.sh" --title "OpenClaw Heal" \
    "PASS on $HOSTNAME_VAL — evidence: $EVIDENCE_DIR" 2>/dev/null || true
fi

exit 0

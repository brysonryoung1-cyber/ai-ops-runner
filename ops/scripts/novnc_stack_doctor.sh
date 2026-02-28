#!/usr/bin/env bash
# novnc_stack_doctor — Deterministic health check: 6080 listening + /novnc/vnc.html 200 + WSS probes >=10s.
#
# Writes PROOF artifacts to artifacts/novnc_debug/novnc_stack_doctor/<run_id>/
# Exit 0 only when all checks PASS. Optionally starts openclaw-novnc if not running (--ensure-up).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-novnc_doctor_$(date -u +%Y%m%dT%H%M%SZ)}"
TS_HOSTNAME="${OPENCLAW_TS_HOSTNAME:-aiops-1.tailc75c62.ts.net}"
ARTIFACTS="${OPENCLAW_ARTIFACTS_ROOT:-$ROOT_DIR/artifacts}"
OUT_DIR="$ARTIFACTS/novnc_debug/novnc_stack_doctor/$RUN_ID"
ENSURE_UP=0
[[ "${1:-}" = "--ensure-up" ]] && ENSURE_UP=1

mkdir -p "$OUT_DIR"

# 1. Check 6080 listening
check_6080() {
  if ss -tln 2>/dev/null | grep -qE ":6080[^0-9]|6080 "; then
    echo "true"
  elif netstat -tln 2>/dev/null | grep -qE "6080"; then
    echo "true"
  else
    echo "false"
  fi
}

PORT_6080_OK="$(check_6080)"

# If not listening and --ensure-up: start openclaw-novnc, wait, recheck
if [ "$PORT_6080_OK" = "false" ] && [ "$ENSURE_UP" -eq 1 ]; then
  if systemctl start openclaw-novnc 2>/dev/null; then
    echo "Started openclaw-novnc; waiting 10s for 6080..."
    sleep 10
    PORT_6080_OK="$(check_6080)"
  fi
fi

echo "6080_listening=$PORT_6080_OK" > "$OUT_DIR/port_check.txt"

# 2. Run novnc_connectivity_audit (HTTP 200 + WSS probes)
AUDIT_PASS=0
if [ "$PORT_6080_OK" = "true" ]; then
  if OPENCLAW_TS_HOSTNAME="$TS_HOSTNAME" python3 "$SCRIPT_DIR/novnc_connectivity_audit.py" \
    --run-id "${RUN_ID}_audit" --host "$TS_HOSTNAME" 2>/dev/null; then
    AUDIT_PASS=1
  fi
  [ -f "$ARTIFACTS/novnc_debug/ws_probe/${RUN_ID}_audit/ws_probe.json" ] && \
    cp "$ARTIFACTS/novnc_debug/ws_probe/${RUN_ID}_audit/ws_probe.json" "$OUT_DIR/" 2>/dev/null || true
fi

# 2b. Hop-by-hop WebSocket upgrade probe (diagnostic on failure, validation on success)
HOP_PROBE="$SCRIPT_DIR/ws_upgrade_hop_probe.sh"
if [ -f "$HOP_PROBE" ]; then
  OPENCLAW_HOP_PROBE_RUN_ID="${RUN_ID}_hop" OPENCLAW_TS_HOSTNAME="$TS_HOSTNAME" \
    bash "$HOP_PROBE" > "$OUT_DIR/hop_probe.log" 2>&1 || true
  HOP_LATEST="$ARTIFACTS/novnc_debug/ws_handshake/${RUN_ID}_hop"
  [ -f "$HOP_LATEST/result.json" ] && cp "$HOP_LATEST/result.json" "$OUT_DIR/hop_result.json" 2>/dev/null || true
  [ -f "$HOP_LATEST/summary.md" ] && cp "$HOP_LATEST/summary.md" "$OUT_DIR/hop_summary.md" 2>/dev/null || true
fi

# 3. Write PROOF
cat > "$OUT_DIR/PROOF.md" << EOF
# novnc_stack_doctor

**Run ID:** $RUN_ID
**Timestamp:** $(date -u +%Y-%m-%dT%H:%M:%SZ)

## Checks
- 6080 listening: $PORT_6080_OK
- /novnc/vnc.html HTTP 200: $([ "$AUDIT_PASS" -eq 1 ] && echo "PASS" || echo "FAIL")
- WSS /websockify >=10s: $([ "$AUDIT_PASS" -eq 1 ] && echo "PASS" || echo "FAIL")
- WSS /novnc/websockify >=10s: $([ "$AUDIT_PASS" -eq 1 ] && echo "PASS" || echo "FAIL")
- Hop probe (A/B/C/D 101): $([ -f "$OUT_DIR/hop_result.json" ] && python3 -c "import json; d=json.load(open('$OUT_DIR/hop_result.json')); print('PASS' if d.get('all_101') else 'FAIL')" 2>/dev/null || echo "SKIP")

**Overall:** $([ "$PORT_6080_OK" = "true" ] && [ "$AUDIT_PASS" -eq 1 ] && echo "PASS" || echo "FAIL")
EOF

if [ "$PORT_6080_OK" = "true" ] && [ "$AUDIT_PASS" -eq 1 ]; then
  echo '{"ok":true,"run_id":"'"$RUN_ID"'","6080_listening":true,"proof":"'"$OUT_DIR"'/PROOF.md"}' | tee "$OUT_DIR/result.json"
  exit 0
fi

echo '{"ok":false,"run_id":"'"$RUN_ID"'","6080_listening":"'"$PORT_6080_OK"'","proof":"'"$OUT_DIR"'/PROOF.md"}' | tee "$OUT_DIR/result.json"
exit 1

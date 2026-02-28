#!/usr/bin/env bash
# openclaw_novnc_doctor.sh — noVNC doctor: framebuffer guard + WS stability (local + tailnet).
#
# --fast: Skip framebuffer warm-up (1 attempt), shorter WS hold (3s). ~25s vs ~90s. For autopilot precheck.
#
# Runs novnc_framebuffer_guard, then novnc_ws_stability_check for BOTH:
#   - ws://127.0.0.1:6080/websockify (local)
#   - ws://<tailnet_host>:6080/websockify (tailnet)
# PASS only when framebuffer guard PASS AND both WS checks PASS (hold >= 10s each).
# On tailnet WS fail: restart openclaw-novnc, retry up to 3 times.
# On fail: collects diagnostics to artifacts/novnc_debug/<run_id>/.
# Always writes framebuffer.png to artifact_dir on PASS.
# Fail-closed: error_class=NOVNC_WS_TAILNET_FAILED with artifact_dir.
set -euo pipefail

# Load canonical config
if [ -f /etc/ai-ops-runner/config/novnc_display.env ]; then
  set -a
  # shellcheck source=/dev/null
  source /etc/ai-ops-runner/config/novnc_display.env
  set +a
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_novnc_doctor}"
FAST_MODE=0
[[ "${1:-}" = "--fast" ]] && FAST_MODE=1
FB_GUARD="$SCRIPT_DIR/guards/novnc_framebuffer_guard.sh"
COLLECT_SCRIPT="$SCRIPT_DIR/scripts/novnc_collect_diagnostics.sh"
WS_CHECK="$SCRIPT_DIR/scripts/novnc_ws_stability_check.py"
NOVNC_PORT="${OPENCLAW_NOVNC_PORT:-${NOVNC_PORT:-6080}}"
VNC_PORT="${OPENCLAW_NOVNC_VNC_PORT:-${VNC_PORT:-5900}}"
DISPLAY_NUM="${OPENCLAW_NOVNC_DISPLAY:-${DISPLAY:-:99}}"
ART_DIR="$ROOT_DIR/artifacts/novnc_debug/$RUN_ID"
MAX_WS_RETRIES=3

mkdir -p "$ART_DIR"

# Get Tailscale URL for noVNC. Prefer HTTPS /novnc path (same origin as HQ) to avoid mixed-content blank.
_get_novnc_url() {
  if command -v tailscale >/dev/null 2>&1; then
    local dns
    dns="$(tailscale status --json 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    name = (d.get('Self') or {}).get('DNSName', '').rstrip('.')
    print(name if name else '')
except: pass
" 2>/dev/null)"
    if [ -n "$dns" ] && [[ "$dns" == *".ts.net" ]]; then
      # Canonical: https same-host /novnc + path=/websockify (WS upgrade via Tailscale Serve /websockify)
      echo "https://${dns}/novnc/vnc.html?autoconnect=1&reconnect=true&reconnect_delay=2000&path=/websockify"
      return
    fi
  fi
  echo "https://<TAILSCALE_HOST>/novnc/vnc.html?autoconnect=1&reconnect=true&reconnect_delay=2000&path=/websockify"
}

NOVNC_URL="$(_get_novnc_url)"

# Run framebuffer guard (pass DISPLAY + VNC for canonical config)
[[ "$FAST_MODE" -eq 1 ]] && export OPENCLAW_NOVNC_DOCTOR_FAST=1
if ! OPENCLAW_RUN_ID="$RUN_ID" OPENCLAW_NOVNC_PORT="$NOVNC_PORT" OPENCLAW_NOVNC_DISPLAY="$DISPLAY_NUM" OPENCLAW_NOVNC_VNC_PORT="$VNC_PORT" "$FB_GUARD" >"$ART_DIR/guard_result.json" 2>/dev/null; then
  if [ -x "$COLLECT_SCRIPT" ]; then
    OPENCLAW_RUN_ID="$RUN_ID" OPENCLAW_NOVNC_PORT="$NOVNC_PORT" "$COLLECT_SCRIPT" 2>/dev/null || true
  fi
  FB_FAIL="$(python3 -c "
import json
try:
    d = json.load(open('$ART_DIR/guard_result.json'))
    print(d.get('fail_reason', 'framebuffer_guard_failed'))
except: print('framebuffer_guard_failed')
" 2>/dev/null)" || FB_FAIL="framebuffer_guard_failed"
  echo "{\"ok\":false,\"result\":\"FAIL\",\"error_class\":\"${FB_FAIL}\",\"novnc_url\":\"$NOVNC_URL\",\"artifact_dir\":\"artifacts/novnc_debug/$RUN_ID\"}"
  exit 1
fi

# WS stability: local (direct 6080) + tailnet (WSS over 443, same as browser)
_run_ws_check() {
  local ws_hold=10
  [[ "$FAST_MODE" -eq 1 ]] && ws_hold=3
  # Prefer WSS-over-443 for tailnet (novnc_ws_probe) when available — matches browser path
  local ts_host=""
  ts_host="$(tailscale status --json 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print((d.get('Self') or {}).get('DNSName', '').rstrip('.') or '')
except: pass
" 2>/dev/null)" || true
  if [ -n "$ts_host" ] && [ -x "$SCRIPT_DIR/scripts/novnc_ws_probe.py" ]; then
    OPENCLAW_NOVNC_PORT="$NOVNC_PORT" OPENCLAW_WS_STABILITY_HOLD_SEC="$ws_hold" python3 "$WS_CHECK" --local 2>/dev/null > "$ART_DIR/ws_local.json" || echo '{"ok":false}' > "$ART_DIR/ws_local.json"
    OPENCLAW_TS_HOSTNAME="$ts_host" OPENCLAW_WS_PROBE_HOLD_SEC="$ws_hold" python3 "$SCRIPT_DIR/scripts/novnc_ws_probe.py" --host "$ts_host" --hold "$ws_hold" --all 2>/dev/null > "$ART_DIR/ws_wss.json" || echo '{"all_ok":false}' > "$ART_DIR/ws_wss.json"
    python3 -c "
import json
local = json.load(open('$ART_DIR/ws_local.json'))
wss = json.load(open('$ART_DIR/ws_wss.json'))
tailnet_ok = wss.get('all_ok', False)
local['tailnet'] = {'ok': tailnet_ok, 'method': 'wss_443'} if tailnet_ok else {'ok': False, 'close_reason': 'wss_probe_failed'}
local['ws_stability_local'] = 'verified' if local.get('ok') else 'failed'
local['ws_stability_tailnet'] = 'verified' if tailnet_ok else 'failed'
local['ok'] = local.get('ok') and tailnet_ok
print(json.dumps(local))
" 2>/dev/null
    return
  fi
  # Fallback: original --all (direct 6080 for both)
  OPENCLAW_NOVNC_PORT="$NOVNC_PORT" OPENCLAW_WS_STABILITY_HOLD_SEC="$ws_hold" python3 "$WS_CHECK" --all 2>/dev/null
}

WS_FAIL_REASON=""
for attempt in $(seq 1 "$MAX_WS_RETRIES"); do
  if _run_ws_check | tee "$ART_DIR/ws_stability.json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
ok = d.get('ok') and d.get('ws_stability_local') == 'verified' and d.get('ws_stability_tailnet') == 'verified'
sys.exit(0 if ok else 1)
" 2>/dev/null; then
    cp "$ART_DIR/ws_stability.json" "$ART_DIR/ws_check.json" 2>/dev/null || true
    echo "{\"ok\":true,\"result\":\"PASS\",\"ws_stability_local\":\"verified\",\"ws_stability_tailnet\":\"verified\",\"novnc_url\":\"$NOVNC_URL\",\"artifact_dir\":\"artifacts/novnc_debug/$RUN_ID\"}"
    exit 0
  fi

  # Parse failure
  WS_FAIL_REASON="$(python3 -c "
import json
try:
    d = json.load(open('$ART_DIR/ws_stability.json'))
    local = d.get('local', {})
    tailnet = d.get('tailnet', {})
    if not local.get('ok'):
        r = local.get('close_reason') or ('code_' + str(local.get('close_code', '')))
        print('local:' + str(r)[:80])
    elif not tailnet.get('ok'):
        r = tailnet.get('close_reason') or ('code_' + str(tailnet.get('close_code', '')))
        print('tailnet:' + str(r)[:80])
    else:
        print('unknown')
except Exception as e:
    print('parse_error:' + str(e)[:60])
" 2>/dev/null)" || WS_FAIL_REASON="ws_check_failed"

  if [ "$attempt" -lt "$MAX_WS_RETRIES" ]; then
    echo "novnc_doctor: WS stability FAIL ($WS_FAIL_REASON), restarting + retry $attempt/$MAX_WS_RETRIES" >&2
    systemctl restart openclaw-novnc 2>/dev/null || true
    sleep 3
  fi
done

# Exhausted retries: fail-closed
if [ -x "$COLLECT_SCRIPT" ]; then
  OPENCLAW_RUN_ID="$RUN_ID" OPENCLAW_NOVNC_PORT="$NOVNC_PORT" "$COLLECT_SCRIPT" 2>/dev/null || true
fi
WS_FAIL_REASON="$(python3 -c "
import json
try:
    d = json.load(open('$ART_DIR/ws_stability.json'))
    local = d.get('local', {})
    tailnet = d.get('tailnet', {})
    if not local.get('ok'):
        print('local:' + str(local.get('close_reason', 'unknown'))[:80])
    elif not tailnet.get('ok'):
        print('tailnet:' + str(tailnet.get('close_reason', 'unknown'))[:80])
    else:
        print('unknown')
except: print('ws_check_failed')
" 2>/dev/null)" || WS_FAIL_REASON="ws_check_failed"

echo "{\"ok\":false,\"result\":\"FAIL\",\"error_class\":\"NOVNC_WS_TAILNET_FAILED\",\"ws_stability\":\"$WS_FAIL_REASON\",\"novnc_url\":\"$NOVNC_URL\",\"artifact_dir\":\"artifacts/novnc_debug/$RUN_ID\"}"
exit 1

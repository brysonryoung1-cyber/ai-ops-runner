#!/usr/bin/env bash
# openclaw_novnc_doctor.sh — noVNC doctor: framebuffer guard + WS stability check.
#
# Runs novnc_framebuffer_guard, then novnc_ws_stability_check (hold >= 10s).
# PASS only when both pass. On WS fail: restart + retry once, then fail-closed.
# On fail: collects diagnostics to artifacts/novnc_debug/<run_id>/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_novnc_doctor}"
FB_GUARD="$SCRIPT_DIR/guards/novnc_framebuffer_guard.sh"
COLLECT_SCRIPT="$SCRIPT_DIR/scripts/novnc_collect_diagnostics.sh"
WS_CHECK="$SCRIPT_DIR/scripts/novnc_ws_stability_check.py"
NOVNC_PORT="${OPENCLAW_NOVNC_PORT:-6080}"
ART_DIR="$ROOT_DIR/artifacts/novnc_debug/$RUN_ID"

mkdir -p "$ART_DIR"

# Get Tailscale URL for noVNC
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
      echo "http://${dns}:${NOVNC_PORT}/vnc.html?autoconnect=1"
      return
    fi
  fi
  echo "http://<TAILSCALE_IP>:$NOVNC_PORT/vnc.html?autoconnect=1"
}

NOVNC_URL="$(_get_novnc_url)"

# Run framebuffer guard
if ! OPENCLAW_RUN_ID="$RUN_ID" OPENCLAW_NOVNC_PORT="$NOVNC_PORT" "$FB_GUARD" >"$ART_DIR/guard_result.json" 2>/dev/null; then
  if [ -x "$COLLECT_SCRIPT" ]; then
    OPENCLAW_RUN_ID="$RUN_ID" OPENCLAW_NOVNC_PORT="$NOVNC_PORT" "$COLLECT_SCRIPT" 2>/dev/null || true
  fi
  echo "{\"ok\":false,\"result\":\"FAIL\",\"novnc_url\":\"$NOVNC_URL\",\"artifact_dir\":\"artifacts/novnc_debug/$RUN_ID\"}"
  exit 1
fi

# WS stability check: hold >= 10s; on fail: restart + retry once
_run_ws_check() {
  OPENCLAW_NOVNC_PORT="$NOVNC_PORT" python3 "$WS_CHECK" 2>/dev/null
}
WS_FAIL_REASON=""
if ! _run_ws_check | tee "$ART_DIR/ws_stability.json" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('ok') else 1)" 2>/dev/null; then
  WS_FAIL_REASON="$(python3 -c "import json; d=json.load(open('$ART_DIR/ws_stability.json')); print(d.get('close_reason','unknown') or ('code_'+str(d.get('close_code',''))))" 2>/dev/null || echo "ws_check_failed")"
  echo "novnc_doctor: WS stability FAIL ($WS_FAIL_REASON), restarting + retry" >&2
  systemctl restart openclaw-novnc 2>/dev/null || true
  sleep 3
  if ! _run_ws_check | tee "$ART_DIR/ws_stability.json" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('ok') else 1)" 2>/dev/null; then
    if [ -x "$COLLECT_SCRIPT" ]; then
      OPENCLAW_RUN_ID="${RUN_ID}" OPENCLAW_NOVNC_PORT="$NOVNC_PORT" "$COLLECT_SCRIPT" 2>/dev/null || true
    fi
    WS_FAIL_REASON="$(python3 -c "import json; d=json.load(open('$ART_DIR/ws_stability.json')); print(d.get('close_reason','unknown') or ('code_'+str(d.get('close_code',''))))" 2>/dev/null || echo "ws_check_failed")"
    echo "{\"ok\":false,\"result\":\"FAIL\",\"ws_stability\":\"$WS_FAIL_REASON\",\"novnc_url\":\"$NOVNC_URL\",\"artifact_dir\":\"artifacts/novnc_debug/$RUN_ID\"}"
    exit 1
  fi
fi

echo "{\"ok\":true,\"result\":\"PASS\",\"ws_stability\":\"verified\",\"novnc_url\":\"$NOVNC_URL\",\"artifact_dir\":\"artifacts/novnc_debug/$RUN_ID\"}"
exit 0

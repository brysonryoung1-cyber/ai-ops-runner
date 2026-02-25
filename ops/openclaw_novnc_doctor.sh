#!/usr/bin/env bash
# openclaw_novnc_doctor.sh — noVNC doctor: run framebuffer guard, write artifacts, return PASS/FAIL + noVNC URL.
#
# Runs novnc_framebuffer_guard. On pass: emits JSON with ok, novnc_url, artifact_dir.
# On fail: collects diagnostics to artifacts/novnc_debug/<run_id>/, emits fail + artifact path.
# Used by HQ Actions and openclaw_hq_audit.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_novnc_doctor}"
FB_GUARD="$SCRIPT_DIR/guards/novnc_framebuffer_guard.sh"
COLLECT_SCRIPT="$SCRIPT_DIR/scripts/novnc_collect_diagnostics.sh"
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
if OPENCLAW_RUN_ID="$RUN_ID" "$FB_GUARD" >"$ART_DIR/guard_result.json" 2>/dev/null; then
  echo "{\"ok\":true,\"result\":\"PASS\",\"novnc_url\":\"$NOVNC_URL\",\"artifact_dir\":\"artifacts/novnc_debug/$RUN_ID\"}"
  exit 0
fi

# Fail: collect full diagnostics
if [ -x "$COLLECT_SCRIPT" ]; then
  OPENCLAW_RUN_ID="${RUN_ID}_fail" "$COLLECT_SCRIPT" 2>/dev/null || true
fi

echo "{\"ok\":false,\"result\":\"FAIL\",\"novnc_url\":\"$NOVNC_URL\",\"artifact_dir\":\"artifacts/novnc_debug/$RUN_ID\"}"
exit 1

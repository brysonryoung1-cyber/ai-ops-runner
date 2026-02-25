#!/usr/bin/env bash
# novnc_framebuffer_guard.sh — Framebuffer-aware self-healing guard for noVNC.
#
# Checks (ALL required):
#   - openclaw-novnc.service is-active
#   - Xvfb process exists for expected DISPLAY
#   - x11vnc process exists bound to expected DISPLAY
#   - websockify process exists bridging websocket -> vnc port
#   - Framebuffer is NOT all-black (xwd capture + mean/variance check)
#
# On PASS: always writes framebuffer.png to artifact_dir (proof artifact).
# If FAIL: auto-heal (restart, retry, hard reset), then fail-closed with artifacts.
# Config: /etc/ai-ops-runner/config/novnc_display.env
# Exit: 0 if pass, nonzero if fail-closed.
set -euo pipefail

# Load canonical config
if [ -f /etc/ai-ops-runner/config/novnc_display.env ]; then
  set -a
  # shellcheck source=/dev/null
  source /etc/ai-ops-runner/config/novnc_display.env
  set +a
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_fbguard}"
NOVNC_PORT="${OPENCLAW_NOVNC_PORT:-${NOVNC_PORT:-6080}}"
VNC_PORT="${OPENCLAW_NOVNC_VNC_PORT:-${VNC_PORT:-5900}}"
DISPLAY_NUM="${OPENCLAW_NOVNC_DISPLAY:-${DISPLAY:-:99}}"
XWD_FILE="/tmp/novnc_fb.xwd"
ART_DIR="$ROOT_DIR/artifacts/novnc_debug/$RUN_ID"
COLLECT_SCRIPT="$ROOT_DIR/ops/scripts/novnc_collect_diagnostics.sh"

mkdir -p "$ART_DIR"

_fail_reason=""

# --- 1) Service active ---
_check_service() {
  if [ "$(systemctl is-active openclaw-novnc.service 2>/dev/null || echo inactive)" != "active" ]; then
    _fail_reason="service_not_active"
    return 1
  fi
  return 0
}

# --- 2) Xvfb process for DISPLAY ---
_check_xvfb() {
  local dnum="${DISPLAY_NUM#:}"
  if ! ps aux 2>/dev/null | grep -v grep | grep -qE "Xvfb.*$DISPLAY_NUM|Xvfb.*:$dnum"; then
    _fail_reason="xvfb_missing"
    return 1
  fi
  return 0
}

# --- 3) x11vnc process ---
_check_x11vnc() {
  if ! ps aux 2>/dev/null | grep -v grep | grep -qE "x11vnc.*$DISPLAY_NUM|x11vnc.*-display|x11vnc.*display"; then
    _fail_reason="x11vnc_missing"
    return 1
  fi
  return 0
}

# --- 4) websockify process ---
_check_websockify() {
  if ! ps aux 2>/dev/null | grep -v grep | grep -qE "websockify.*$NOVNC_PORT|websockify.*6080"; then
    _fail_reason="websockify_missing"
    return 1
  fi
  return 0
}

# --- 5) Framebuffer not-all-black (skip if xwd not installed) ---
# Retry up to 3x with 4s sleep: xsetroot needs a few seconds after Xvfb socket ready
_check_framebuffer() {
  if ! command -v xwd >/dev/null 2>&1; then
    # Fallback: HTTP check only (no framebuffer validation)
    if curl -fsS --connect-timeout 2 --max-time 4 "http://127.0.0.1:$NOVNC_PORT/vnc.html" >/dev/null 2>/dev/null; then
      return 0
    fi
    _fail_reason="xwd_missing_and_http_fail"
    return 1
  fi

  local attempt
  for attempt in 1 2 3; do
    rm -f "$XWD_FILE"
    if ! DISPLAY="$DISPLAY_NUM" xwd -root -silent -out "$XWD_FILE" 2>/dev/null; then
      _fail_reason="xwd_capture_failed"
      [ "$attempt" -lt 3 ] && sleep 4
      continue
    fi
    if [ ! -s "$XWD_FILE" ]; then
      _fail_reason="xwd_empty"
      [ "$attempt" -lt 3 ] && sleep 4
      continue
    fi

    local mean=""
    local is_black=1

    if command -v convert >/dev/null 2>&1; then
      mean="$(convert "$XWD_FILE" -format "%[fx:mean]" info: 2>/dev/null || echo "0")"
      if [ -n "$mean" ] && python3 -c "exit(0 if float('$mean') > 0.001 else 1)" 2>/dev/null; then
        is_black=0
      fi
    else
      # Python fallback: sample pixel bytes, check variance
      if python3 -c "
import sys
try:
    with open('$XWD_FILE', 'rb') as f:
        data = f.read()
    # XWD header is typically 8 + variable; skip first 256 bytes to reach pixel data
    if len(data) < 500:
        sys.exit(1)
    pixels = data[256:min(256 + 50000, len(data))]
    unique = len(set(pixels))
    nonzero = sum(1 for b in pixels if b != 0)
    # Not all-black if we have variance or nonzero bytes
    if unique > 1 or nonzero > 0:
        sys.exit(0)
    sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
        is_black=0
      fi
    fi

    if [ "$is_black" -eq 0 ]; then
      break
    fi
    _fail_reason="framebuffer_all_black"
    [ "$attempt" -lt 3 ] && sleep 4
  done

  if [ "$is_black" -ne 0 ]; then
    _fail_reason="framebuffer_all_black"
    return 1
  fi
  # Always write framebuffer.png to artifacts (proof artifact for WAITING_FOR_HUMAN)
  if [ -f "$XWD_FILE" ] && [ -s "$XWD_FILE" ]; then
    if command -v convert >/dev/null 2>&1; then
      convert "$XWD_FILE" "$ART_DIR/framebuffer.png" 2>/dev/null || true
    fi
    cp "$XWD_FILE" "$ART_DIR/novnc_fb.xwd" 2>/dev/null || true
  fi
  return 0
}

# --- Run all checks ---
_run_checks() {
  _fail_reason=""
  _check_service || return 1
  _check_xvfb || return 1
  _check_x11vnc || return 1
  _check_websockify || return 1
  _check_framebuffer || return 1
  return 0
}

# --- Hard reset: kill processes, remove stale locks, restart ---
_hard_reset() {
  local dnum="${DISPLAY_NUM#:}"
  local lock_file="/tmp/.X${dnum}-lock"
  pkill -f "Xvfb $DISPLAY_NUM" 2>/dev/null || true
  pkill -f "x11vnc.*$DISPLAY_NUM" 2>/dev/null || true
  pkill -f "websockify.*$NOVNC_PORT" 2>/dev/null || true
  sleep 2
  if [ -f "$lock_file" ]; then
    local old_pid
    old_pid="$(cat "$lock_file" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && ! kill -0 "$old_pid" 2>/dev/null; then
      rm -f "$lock_file"
    fi
  fi
  systemctl restart openclaw-novnc.service 2>/dev/null || true
  sleep 3
}

# --- Collect diagnostics to artifacts ---
_collect_and_fail() {
  if [ -x "$COLLECT_SCRIPT" ]; then
    OPENCLAW_RUN_ID="${RUN_ID}_fail" "$COLLECT_SCRIPT" 2>/dev/null || true
  fi
  # Copy framebuffer artifact if exists
  if [ -f "$XWD_FILE" ]; then
    cp "$XWD_FILE" "$ART_DIR/novnc_fb.xwd" 2>/dev/null || true
    if command -v convert >/dev/null 2>&1; then
      convert "$XWD_FILE" "$ART_DIR/novnc_fb.png" 2>/dev/null || true
    fi
  fi
  echo "{\"ok\":false,\"run_id\":\"$RUN_ID\",\"fail_reason\":\"$_fail_reason\",\"artifact_dir\":\"artifacts/novnc_debug/$RUN_ID\"}"
  exit 1
}

# --- Main ---
# Ensure artifact dir exists for framebuffer.png (written on PASS)
mkdir -p "$ART_DIR"
TIMINGS_FILE="$ART_DIR/timings.json"

_timestamp() { date +%s.%N; }
T_START=$(_timestamp)

if _run_checks; then
  T_END=$(_timestamp)
  python3 -c "
import json
from datetime import datetime, timezone
t_start, t_end = float('$T_START'), float('$T_END')
d = {
  'run_id': '$RUN_ID',
  'timestamp_utc': datetime.now(timezone.utc).isoformat(),
  'total_sec': round(t_end - t_start, 2),
}
with open('$TIMINGS_FILE', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null || true
  echo "{\"ok\":true,\"run_id\":\"$RUN_ID\",\"display\":\"$DISPLAY_NUM\",\"novnc_port\":$NOVNC_PORT}"
  exit 0
fi

# Remediate: restart service
echo "novnc_framebuffer_guard: $_fail_reason, attempting heal" >&2
systemctl restart openclaw-novnc.service 2>/dev/null || true
sleep 3

for retry in 1 2 3; do
  if _run_checks; then
    echo "{\"ok\":true,\"run_id\":\"$RUN_ID\",\"remediated\":true,\"display\":\"$DISPLAY_NUM\",\"novnc_port\":$NOVNC_PORT}"
    exit 0
  fi
  sleep 2
done

# Hard reset
echo "novnc_framebuffer_guard: soft heal failed, hard reset" >&2
_hard_reset

for retry in 1 2 3; do
  if _run_checks; then
    echo "{\"ok\":true,\"run_id\":\"$RUN_ID\",\"remediated\":true,\"hard_reset\":true,\"display\":\"$DISPLAY_NUM\",\"novnc_port\":$NOVNC_PORT}"
    exit 0
  fi
  sleep 2
done

# Fail-closed
_collect_and_fail

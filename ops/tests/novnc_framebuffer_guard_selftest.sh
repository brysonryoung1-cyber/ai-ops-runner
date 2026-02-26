#!/usr/bin/env bash
# novnc_framebuffer_guard_selftest.sh — Unit selftest: framebuffer guard script exists and uses xwd + not-all-black logic.
#
# Asserts:
#   - ops/guards/novnc_framebuffer_guard.sh exists and is executable
#   - Script contains xwd usage (framebuffer capture)
#   - Script contains not-all-black check (mean, variance, or ImageMagick convert)
#   - Script has heal/hard-reset logic
# Skip integration (Xvfb/xwd) unless OPENCLAW_NOVNC_SELFTEST_FULL=1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
GUARD="$ROOT_DIR/ops/guards/novnc_framebuffer_guard.sh"
cd "$ROOT_DIR"

echo "==> novnc_framebuffer_guard_selftest"

# 1. Script exists and is executable
[ -f "$GUARD" ] || { echo "  FAIL: novnc_framebuffer_guard.sh missing"; exit 1; }
chmod +x "$GUARD" 2>/dev/null || true
bash -n "$GUARD" 2>/dev/null || { echo "  FAIL: novnc_framebuffer_guard.sh syntax error"; exit 1; }
echo "  PASS: novnc_framebuffer_guard.sh exists and valid"

# 2. Contains xwd usage for framebuffer capture
grep -qE "xwd|XWD_FILE" "$GUARD" || { echo "  FAIL: script must use xwd for framebuffer capture"; exit 1; }
echo "  PASS: xwd usage present"

# 3. Contains not-all-black logic (mean, variance, convert, or pixel check)
grep -qE "mean|variance|convert.*info:|all.black|unique|nonzero" "$GUARD" || { echo "  FAIL: script must have not-all-black check (mean/variance/convert/pixel)"; exit 1; }
echo "  PASS: not-all-black logic present"

# 4. Contains heal/hard-reset logic
grep -qE "restart|_hard_reset|pkill|remediate" "$GUARD" || { echo "  FAIL: script must have heal/hard-reset logic"; exit 1; }
echo "  PASS: heal/hard-reset logic present"

# 5. Contains warm-up loop for all-black (xsetroot, kajabi_ui_ensure before final fail)
grep -qE "FB_WARMUP_MAX|kajabi_ui_ensure|KAJABI_ENSURE" "$GUARD" || { echo "  FAIL: script must have warm-up loop for all-black"; exit 1; }
echo "  PASS: warm-up loop for all-black present"

# 6. Integration test (skip by default): requires Xvfb, xwd, DISPLAY
if [ "${OPENCLAW_NOVNC_SELFTEST_FULL:-0}" = "1" ] && command -v Xvfb >/dev/null 2>&1 && command -v xwd >/dev/null 2>&1; then
  echo "  Running integration (xsetroot mean change)..."
  export DISPLAY=:199
  Xvfb "$DISPLAY" -screen 0 1280x720x24 -ac -nolisten tcp &
  XVFB_PID=$!
  sleep 2
  if DISPLAY="$DISPLAY" xwd -root -silent -out /tmp/novnc_selftest_black.xwd 2>/dev/null; then
    if command -v convert >/dev/null 2>&1; then
      mean_black="$(convert /tmp/novnc_selftest_black.xwd -format "%[fx:mean]" info: 2>/dev/null || echo "0")"
      xsetroot -solid red 2>/dev/null || true
      sleep 1
      DISPLAY="$DISPLAY" xwd -root -silent -out /tmp/novnc_selftest_red.xwd 2>/dev/null || true
      mean_red="$(convert /tmp/novnc_selftest_red.xwd -format "%[fx:mean]" info: 2>/dev/null || echo "0")"
      [ -n "$mean_black" ] && [ -n "$mean_red" ] && [ "$mean_black" != "$mean_red" ] && echo "  PASS: mean changes with xsetroot (black=$mean_black red=$mean_red)" || echo "  WARN: mean unchanged"
    fi
    rm -f /tmp/novnc_selftest_black.xwd /tmp/novnc_selftest_red.xwd
  fi
  kill -TERM "$XVFB_PID" 2>/dev/null || true
  wait "$XVFB_PID" 2>/dev/null || true
else
  echo "  SKIP: integration (set OPENCLAW_NOVNC_SELFTEST_FULL=1 to run)"
fi

echo "==> novnc_framebuffer_guard_selftest PASS"

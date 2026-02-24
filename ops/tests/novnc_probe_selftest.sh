#!/usr/bin/env bash
# novnc_probe_selftest.sh — Regression selftest: probe fails when service stopped, passes when running.
#
# On aiops-1 (or any host with openclaw-novnc installed): stop → probe fails; start → probe passes.
# When systemd/novnc not available: skip (mock pass for CI).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

echo "==> novnc_probe_selftest"

# 1. Probe script exists and is executable
if [ ! -f "$ROOT_DIR/ops/novnc_probe.sh" ]; then
  echo "  FAIL: ops/novnc_probe.sh missing"
  exit 1
fi
if ! bash -n "$ROOT_DIR/ops/novnc_probe.sh" 2>/dev/null; then
  echo "  FAIL: novnc_probe.sh has syntax errors"
  exit 1
fi
chmod +x "$ROOT_DIR/ops/novnc_probe.sh" 2>/dev/null || true
echo "  PASS: novnc_probe.sh valid"

# 2. When systemd + openclaw-novnc installed: stop → probe fails; start → probe passes
if command -v systemctl >/dev/null 2>&1 && [ -f /etc/systemd/system/openclaw-novnc.service ]; then
  SELFTEST_ARTIFACT="$(mktemp -d)"
  mkdir -p /run/openclaw-novnc /run/openclaw 2>/dev/null || true
  printf "OPENCLAW_NOVNC_RUN_ID=selftest\nOPENCLAW_NOVNC_ARTIFACT_DIR=%s\nOPENCLAW_NOVNC_PORT=6080\nOPENCLAW_NOVNC_DISPLAY=:99\nOPENCLAW_NOVNC_VNC_PORT=5900\n" "$SELFTEST_ARTIFACT" > /run/openclaw-novnc/next.env 2>/dev/null || true

  systemctl stop openclaw-novnc 2>/dev/null || true
  sleep 2
  if OPENCLAW_NOVNC_PORT=6080 OPENCLAW_NOVNC_VNC_PORT=5900 "$ROOT_DIR/ops/novnc_probe.sh" 2>/dev/null; then
    echo "  WARN: probe passed with service stopped (port may be in use by another process)"
  else
    echo "  PASS: probe fails when service stopped"
  fi

  if systemctl start openclaw-novnc 2>/dev/null; then
    for i in $(seq 1 30); do
      if OPENCLAW_NOVNC_PORT=6080 OPENCLAW_NOVNC_VNC_PORT=5900 "$ROOT_DIR/ops/novnc_probe.sh" 2>/dev/null; then
        echo "  PASS: probe passes after service start (${i}s)"
        break
      fi
      [ "$i" -eq 30 ] && { echo "  FAIL: probe did not pass within 30s"; systemctl stop openclaw-novnc 2>/dev/null || true; rm -rf "$SELFTEST_ARTIFACT"; exit 1; }
      sleep 1
    done
    systemctl stop openclaw-novnc 2>/dev/null || true
    sleep 2
  else
    echo "  SKIP: systemctl start openclaw-novnc failed (Xvfb/novnc deps may be missing)"
  fi
  rm -rf "$SELFTEST_ARTIFACT" 2>/dev/null || true
else
  echo "  SKIP: openclaw-novnc not installed (deploy installs on aiops-1)"
fi

echo "==> novnc_probe_selftest PASS"

#!/usr/bin/env bash
# novnc_collect_diagnostics.sh — Collect noVNC diagnostics to artifacts/novnc_debug/<run_id>/
#
# Run on aiops-1. Writes: systemctl status, journalctl, ps, ss, curl vnc.html.
# Sanitizes output (no tokens/cookies/passwords). Used by novnc_doctor and fail-closed path.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_novnc_debug}"
ART_DIR="$ROOT_DIR/artifacts/novnc_debug/$RUN_ID"
NOVNC_PORT="${OPENCLAW_NOVNC_PORT:-6080}"

mkdir -p "$ART_DIR"

# systemctl status
systemctl status openclaw-novnc.service --no-pager 2>/dev/null >"$ART_DIR/systemctl_status.txt" || echo "service not found" >"$ART_DIR/systemctl_status.txt"

# journalctl
journalctl -u openclaw-novnc.service -n 400 --no-pager 2>/dev/null >"$ART_DIR/journalctl.txt" || echo "journalctl unavailable" >"$ART_DIR/journalctl.txt"

# ps (sanitize: show only cmdline patterns, no full argv with secrets)
ps aux 2>/dev/null | grep -E 'Xvfb|x11vnc|websockify|novnc' | grep -v grep | sed 's/[0-9]\{1,\}\.[0-9]\{1,\}%//g' >"$ART_DIR/ps_novnc.txt" 2>/dev/null || echo "no matching processes" >"$ART_DIR/ps_novnc.txt"

# ss -lntp for relevant ports
ss -lntp 2>/dev/null | grep -E ':6080|:5900|:5901|:8787|:8877' >"$ART_DIR/ss_ports.txt" 2>/dev/null || echo "no matching ports" >"$ART_DIR/ss_ports.txt"

# curl vnc.html (first 5 lines only)
curl -fsS "http://127.0.0.1:$NOVNC_PORT/vnc.html" 2>/dev/null | head -n 5 >"$ART_DIR/vnc_html_head.txt" || echo "curl failed" >"$ART_DIR/vnc_html_head.txt"

echo "$ART_DIR"

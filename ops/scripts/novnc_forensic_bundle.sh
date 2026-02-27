#!/usr/bin/env bash
# novnc_forensic_bundle.sh — Phase 1 evidence: tailscale serve, systemd, ports, curl, WSS probe.
#
# Writes to artifacts/novnc_debug/ws_probe/<run_id>/
#   serve_status.txt, systemd_status.txt, journal_tail.txt, ports.txt
#   ws_probe.json (WSS over 443: /websockify, /novnc/websockify)
#   summary.md
#
# Run on aiops-1. No secrets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d%H%M%S)_forensic}"
ART_DIR="$ROOT_DIR/artifacts/novnc_debug/ws_probe/$RUN_ID"
NOVNC_PORT="${OPENCLAW_NOVNC_PORT:-6080}"
CONSOLE_PORT="${OPENCLAW_CONSOLE_PORT:-8787}"

mkdir -p "$ART_DIR"

TS_HOSTNAME="aiops-1.tailc75c62.ts.net"
if command -v tailscale >/dev/null 2>&1; then
  TS_HOSTNAME="$(tailscale status --json 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print((d.get('Self') or {}).get('DNSName', '').rstrip('.') or 'aiops-1.tailc75c62.ts.net')
except: print('aiops-1.tailc75c62.ts.net')
" 2>/dev/null)"
fi

echo "Collecting forensic bundle to $ART_DIR"

# 1. Tailscale serve
tailscale serve status 2>/dev/null >"$ART_DIR/serve_status.txt" || echo "tailscale serve unavailable" >"$ART_DIR/serve_status.txt"
tailscale serve status --json 2>/dev/null >"$ART_DIR/serve_status.json" || true

# 2. Systemd + journal
systemctl status openclaw-novnc.service --no-pager 2>/dev/null >"$ART_DIR/systemd_status.txt" || echo "service not found" >"$ART_DIR/systemd_status.txt"
journalctl -u openclaw-novnc.service -n 200 --no-pager 2>/dev/null >"$ART_DIR/journal_tail.txt" || echo "journal unavailable" >"$ART_DIR/journal_tail.txt"
systemctl status openclaw-hostd.service --no-pager 2>/dev/null >>"$ART_DIR/systemd_status.txt" || true

# 3. Ports
ss -lntp 2>/dev/null | grep -E ":$NOVNC_PORT|:6080|:8787|:5900" >"$ART_DIR/ports.txt" 2>/dev/null || echo "no matching ports" >"$ART_DIR/ports.txt"
lsof -i ":$NOVNC_PORT" -i ":8787" -i ":5900" 2>/dev/null >>"$ART_DIR/ports.txt" || true

# 4. Curl
curl -fsS "http://127.0.0.1:$CONSOLE_PORT/api/ui/health_public" 2>/dev/null >"$ART_DIR/health_public.json" || echo '{"ok":false}' >"$ART_DIR/health_public.json"
curl -kfsS "https://${TS_HOSTNAME}/api/ui/health_public" 2>/dev/null >"$ART_DIR/health_public_tailnet.json" || echo '{"ok":false}' >"$ART_DIR/health_public_tailnet.json"
HTTP_CODE="$(curl -kfsS -o /dev/null -w "%{http_code}" "https://${TS_HOSTNAME}/novnc/vnc.html" 2>/dev/null)" || HTTP_CODE="000"
echo "novnc_vnc_html_http_code=$HTTP_CODE" >>"$ART_DIR/curl_results.txt"

# 5. WSS probe (WSS over 443 — same as browser)
OPENCLAW_TS_HOSTNAME="$TS_HOSTNAME" OPENCLAW_WS_PROBE_HOLD_SEC=10 \
  python3 "$SCRIPT_DIR/novnc_ws_probe.py" --host "$TS_HOSTNAME" --all 2>/dev/null >"$ART_DIR/ws_probe.json" || echo '{"all_ok":false}' >"$ART_DIR/ws_probe.json"

# 6. Client probe (if run from client machine on tailnet)
if [ -n "${OPENCLAW_CLIENT_PROBE:-}" ]; then
  OPENCLAW_TS_HOSTNAME="$TS_HOSTNAME" python3 "$SCRIPT_DIR/novnc_ws_probe.py" --host "$TS_HOSTNAME" --all 2>/dev/null >"$ART_DIR/client_ws_probe.json" || echo '{"all_ok":false}' >"$ART_DIR/client_ws_probe.json"
fi

# 7. Summary
export _ART_DIR="$ART_DIR" _TS_HOST="$TS_HOSTNAME"
python3 << 'PYEOF'
import json, os
art = os.environ.get("_ART_DIR", "")
ts = os.environ.get("_TS_HOST", "")
with open(art + "/summary.md", "w") as f:
    f.write("# noVNC Forensic Bundle Summary\n\n")
    f.write("Run ID: " + os.path.basename(os.path.dirname(art)) + "\n")
    f.write("Host: " + ts + "\n\n")
    ws = {}
    if os.path.exists(art + "/ws_probe.json"):
        with open(art + "/ws_probe.json") as fp:
            ws = json.load(fp)
    all_ok = ws.get("all_ok", False)
    f.write("## WSS Probe (443): " + ("PASS" if all_ok else "FAIL") + "\n\n")
    for path, r in ws.get("endpoints", {}).items():
        status = "PASS" if r.get("ok") else "FAIL"
        reason = r.get("close_reason") or ""
        f.write("- " + path + ": " + status)
        if reason:
            f.write(" (" + reason[:80] + ")")
        f.write("\n")
    f.write("\n## Artifacts\n")
    for name in ["serve_status.txt", "systemd_status.txt", "journal_tail.txt", "ports.txt", "ws_probe.json"]:
        f.write("- " + name + "\n")
PYEOF

echo "Forensic bundle: $ART_DIR"
echo "Summary:"
cat "$ART_DIR/summary.md" 2>/dev/null || true
exit 0

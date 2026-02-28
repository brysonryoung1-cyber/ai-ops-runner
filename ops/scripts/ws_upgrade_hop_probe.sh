#!/usr/bin/env bash
# ws_upgrade_hop_probe.sh — Deterministic hop-by-hop WebSocket upgrade probe.
#
# Tests each hop in the chain: websockify(6080) → frontdoor(8788) → tailscale(443) → alias(443).
# Captures first-line status + key headers. Writes summary identifying which hop fails.
#
# Artifacts: artifacts/novnc_debug/ws_handshake/<run_id>/
# Run on production host (aiops-1).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="${OPENCLAW_HOP_PROBE_RUN_ID:-hop_$(date -u +%Y%m%dT%H%M%SZ)}"
TS_HOSTNAME="${OPENCLAW_TS_HOSTNAME:-aiops-1.tailc75c62.ts.net}"
ARTIFACTS="${OPENCLAW_ARTIFACTS_ROOT:-$ROOT_DIR/artifacts}"
OUT_DIR="$ARTIFACTS/novnc_debug/ws_handshake/$RUN_ID"
mkdir -p "$OUT_DIR"

ws_probe_hop() {
  local label="$1" url="$2" outfile="$3"
  curl -isS --http1.1 --max-time 8 --connect-timeout 5 \
    -H "Connection: Upgrade" \
    -H "Upgrade: websocket" \
    -H "Sec-WebSocket-Version: 13" \
    -H "Sec-WebSocket-Key: SGVsbG8sIHdvcmxkIQ==" \
    "$url" 2>&1 | head -40 > "$outfile" || true
  local status_line
  status_line="$(head -1 "$outfile" 2>/dev/null | tr -d '\r')"
  echo "$label: $status_line"
}

echo "=== ws_upgrade_hop_probe ($RUN_ID) ==="
echo "Host: $TS_HOSTNAME"
echo ""

# Hop A: websockify backend directly (6080)
ws_probe_hop "A_6080" "http://127.0.0.1:6080/websockify" "$OUT_DIR/A_6080_headers.txt"

# Hop B: frontdoor Caddy (8788)
ws_probe_hop "B_8788" "http://127.0.0.1:8788/websockify" "$OUT_DIR/B_8788_headers.txt"

# Hop C: Tailscale Serve HTTPS (443) — /websockify
ws_probe_hop "C_443"  "https://${TS_HOSTNAME}/websockify"  "$OUT_DIR/C_443_headers.txt"

# Hop D: Tailscale Serve HTTPS (443) — /novnc/websockify alias
ws_probe_hop "D_443_alias" "https://${TS_HOSTNAME}/novnc/websockify" "$OUT_DIR/D_443_alias_headers.txt"

# Bonus: /novnc/vnc.html should be HTTP 200
echo ""
echo "Bonus: /novnc/vnc.html (expect 200)"
curl -isS --max-time 8 --connect-timeout 5 \
  "https://${TS_HOSTNAME}/novnc/vnc.html" 2>&1 | head -25 > "$OUT_DIR/novnc_html_headers.txt" || true
head -1 "$OUT_DIR/novnc_html_headers.txt" 2>/dev/null | tr -d '\r'

# Backend process truth
echo ""
echo "=== Backend process truth ==="
ss -ltnp 2>/dev/null | grep -E '(:6080|:5900|:8788|:8787)' > "$OUT_DIR/ports.txt" || true
cat "$OUT_DIR/ports.txt"

systemctl status openclaw-novnc --no-pager 2>/dev/null | head -15 > "$OUT_DIR/novnc_status.txt" || true
journalctl -u openclaw-novnc -n 200 --no-pager 2>/dev/null > "$OUT_DIR/novnc_journal.txt" || true

systemctl status openclaw-frontdoor --no-pager 2>/dev/null | head -15 > "$OUT_DIR/frontdoor_status.txt" || true
journalctl -u openclaw-frontdoor -n 200 --no-pager 2>/dev/null > "$OUT_DIR/frontdoor_journal.txt" || true

tailscale serve status 2>/dev/null > "$OUT_DIR/tailscale_serve_status.txt" || true
cat "$OUT_DIR/tailscale_serve_status.txt"

# Write summary
extract_status() { head -1 "$1" 2>/dev/null | tr -d '\r' | grep -oE 'HTTP/[0-9.]+ [0-9]+' || echo "UNREACHABLE"; }
extract_server() { grep -i '^Server:' "$1" 2>/dev/null | head -1 | tr -d '\r' || echo "Server: unknown"; }

A_STATUS="$(extract_status "$OUT_DIR/A_6080_headers.txt")"
B_STATUS="$(extract_status "$OUT_DIR/B_8788_headers.txt")"
C_STATUS="$(extract_status "$OUT_DIR/C_443_headers.txt")"
D_STATUS="$(extract_status "$OUT_DIR/D_443_alias_headers.txt")"
A_SERVER="$(extract_server "$OUT_DIR/A_6080_headers.txt")"
B_SERVER="$(extract_server "$OUT_DIR/B_8788_headers.txt")"
C_SERVER="$(extract_server "$OUT_DIR/C_443_headers.txt")"
D_SERVER="$(extract_server "$OUT_DIR/D_443_alias_headers.txt")"

# Classify bucket
BUCKET="UNKNOWN"
if echo "$A_STATUS" | grep -qv "101"; then
  BUCKET="BUCKET_1_BACKEND_WEBSOCKIFY_NOT_UPGRADING"
elif echo "$B_STATUS" | grep -qv "101"; then
  BUCKET="BUCKET_2_FRONTDOOR_ROUTE_ORDER_OR_PROXY_BROKEN"
elif echo "$C_STATUS" | grep -qv "101"; then
  BUCKET="BUCKET_3_TAILSCALE_SERVE_STRIPS_UPGRADE"
elif echo "$D_STATUS" | grep -qv "101"; then
  BUCKET="BUCKET_4_ALIAS_ONLY_BROKEN"
else
  BUCKET="ALL_HOPS_101_OK"
fi

cat > "$OUT_DIR/summary.md" << EOF
# Hop-by-hop WebSocket Upgrade Probe

**Run ID:** $RUN_ID
**Timestamp:** $(date -u +%Y-%m-%dT%H:%M:%SZ)
**Host:** $TS_HOSTNAME

## Results

| Hop | URL | Status | Server |
|-----|-----|--------|--------|
| A (6080) | http://127.0.0.1:6080/websockify | $A_STATUS | $A_SERVER |
| B (8788) | http://127.0.0.1:8788/websockify | $B_STATUS | $B_SERVER |
| C (443)  | https://$TS_HOSTNAME/websockify | $C_STATUS | $C_SERVER |
| D (443 alias) | https://$TS_HOSTNAME/novnc/websockify | $D_STATUS | $D_SERVER |

## Classification
**$BUCKET**

## Artifacts
$(ls -1 "$OUT_DIR"/*.txt 2>/dev/null | while read f; do echo "- $(basename "$f")"; done)
EOF

echo ""
echo "=== Classification: $BUCKET ==="
echo "Summary: $OUT_DIR/summary.md"

# Symlink latest
ln -sfn "$RUN_ID" "$ARTIFACTS/novnc_debug/ws_handshake/latest" 2>/dev/null || true

# JSON result
cat > "$OUT_DIR/result.json" << EOF
{"run_id":"$RUN_ID","bucket":"$BUCKET","A_6080":"$A_STATUS","B_8788":"$B_STATUS","C_443":"$C_STATUS","D_443_alias":"$D_STATUS","all_101":$([ "$BUCKET" = "ALL_HOPS_101_OK" ] && echo true || echo false)}
EOF

[ "$BUCKET" = "ALL_HOPS_101_OK" ] && exit 0 || exit 1

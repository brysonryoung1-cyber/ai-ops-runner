#!/usr/bin/env bash
# state_pack.sh — State Pack generator (single source of truth).
# Collects current system truth for Ask OpenClaw. Read-only, no mutations.
# Writes to artifacts/system/state_pack/<run_id>/
# Output: JSON OCL Result with evidence links (stdout).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ARTIFACTS_ROOT="${OPENCLAW_ARTIFACTS_ROOT:-$ROOT_DIR/artifacts}"
RUN_ID="state_pack_$(date -u +%Y%m%dT%H%M%SZ)_$(od -A n -t x4 -N 2 /dev/urandom 2>/dev/null | tr -d ' ' || echo "$$")"
OUT_DIR="$ARTIFACTS_ROOT/system/state_pack/$RUN_ID"
mkdir -p "$OUT_DIR"

CONSOLE_PORT="${OPENCLAW_CONSOLE_PORT:-8787}"
CONSOLE_BASE="http://127.0.0.1:$CONSOLE_PORT"
FRONTDOOR_PORT="${OPENCLAW_FRONTDOOR_PORT:-8788}"

checks=()
evidence=()

# --- 1. health_public ---
if curl -sf --connect-timeout 3 --max-time 5 "$CONSOLE_BASE/api/ui/health_public" >"$OUT_DIR/health_public.json" 2>/dev/null; then
  checks+=('{"name":"health_public","pass":true,"detail":"OK"}')
  evidence+=("{\"path\":\"artifacts/system/state_pack/$RUN_ID/health_public.json\",\"label\":\"health_public\"}")
else
  echo '{"ok":false}' >"$OUT_DIR/health_public.json"
  checks+=('{"name":"health_public","pass":false,"detail":"curl failed"}')
  evidence+=("{\"path\":\"artifacts/system/state_pack/$RUN_ID/health_public.json\",\"label\":\"health_public\"}")
fi

# --- 2. autopilot_status ---
if curl -sf --connect-timeout 3 --max-time 5 "$CONSOLE_BASE/api/autopilot/status" >"$OUT_DIR/autopilot_status.json" 2>/dev/null; then
  checks+=('{"name":"autopilot_status","pass":true,"detail":"OK"}')
  evidence+=("{\"path\":\"artifacts/system/state_pack/$RUN_ID/autopilot_status.json\",\"label\":\"autopilot_status\"}")
else
  echo '{"ok":false}' >"$OUT_DIR/autopilot_status.json"
  checks+=('{"name":"autopilot_status","pass":false,"detail":"curl failed"}')
  evidence+=("{\"path\":\"artifacts/system/state_pack/$RUN_ID/autopilot_status.json\",\"label\":\"autopilot_status\"}")
fi

# --- 3. tailscale serve ---
if tailscale serve status 2>/dev/null >"$OUT_DIR/tailscale_serve.txt"; then
  tailscale serve status --json 2>/dev/null >"$OUT_DIR/tailscale_serve.json" || true
  checks+=('{"name":"tailscale_serve","pass":true,"detail":"OK"}')
  evidence+=("{\"path\":\"artifacts/system/state_pack/$RUN_ID/tailscale_serve.txt\",\"label\":\"tailscale_serve\"}")
else
  echo "tailscale serve unavailable" >"$OUT_DIR/tailscale_serve.txt"
  checks+=('{"name":"tailscale_serve","pass":false,"detail":"unavailable"}')
  evidence+=("{\"path\":\"artifacts/system/state_pack/$RUN_ID/tailscale_serve.txt\",\"label\":\"tailscale_serve\"}")
fi

# --- 4. ports (8787, 8788, 6080, 5900) ---
(ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null || true) | grep -E ":(8787|8788|6080|5900) " >"$OUT_DIR/ports.txt" || true
if [ -s "$OUT_DIR/ports.txt" ]; then
  checks+=('{"name":"ports","pass":true,"detail":"OK"}')
else
  echo "No listeners on 8787/8788/6080/5900" >"$OUT_DIR/ports.txt"
  checks+=('{"name":"ports","pass":false,"detail":"no listeners"}')
fi
evidence+=("{\"path\":\"artifacts/system/state_pack/$RUN_ID/ports.txt\",\"label\":\"ports\"}")

# --- 5. systemd (novnc, frontdoor, hostd, guard, hq) ---
for unit in openclaw-novnc openclaw-frontdoor openclaw-hostd openclaw-guard; do
  f="$OUT_DIR/systemd_${unit}.txt"
  systemctl status "${unit}.service" --no-pager 2>/dev/null >"$f" || echo "unit ${unit}.service not found" >"$f"
  journalctl -u "${unit}.service" -n 20 --no-pager 2>/dev/null >>"$f" || true
  evidence+=("{\"path\":\"artifacts/system/state_pack/$RUN_ID/systemd_${unit}.txt\",\"label\":\"systemd_$unit\"}")
done
# systemd_hq = frontdoor (serves HQ)
[ -f "$OUT_DIR/systemd_openclaw-frontdoor.txt" ] && cp "$OUT_DIR/systemd_openclaw-frontdoor.txt" "$OUT_DIR/systemd_hq.txt" || echo "hq/frontdoor not found" >"$OUT_DIR/systemd_hq.txt"
evidence+=("{\"path\":\"artifacts/system/state_pack/$RUN_ID/systemd_hq.txt\",\"label\":\"systemd_hq\"}")

# --- 5b. llm_status ---
if curl -sf --connect-timeout 3 --max-time 5 "$CONSOLE_BASE/api/llm/status" >"$OUT_DIR/llm_status.json" 2>/dev/null; then
  checks+=('{"name":"llm_status","pass":true,"detail":"OK"}')
else
  echo '{"ok":false,"providers":[],"config":{"valid":false,"error":"disabled or unreachable"}}' >"$OUT_DIR/llm_status.json"
  checks+=('{"name":"llm_status","pass":false,"detail":"disabled or unreachable"}')
fi
evidence+=("{\"path\":\"artifacts/system/state_pack/$RUN_ID/llm_status.json\",\"label\":\"llm_status\"}")

# --- 5c. novnc_http_check ---
NOVNC_HTTP_CODE="$(curl -sf -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 5 "http://127.0.0.1:$FRONTDOOR_PORT/novnc/vnc.html" 2>/dev/null)" || NOVNC_HTTP_CODE="000"
echo "{\"url\":\"http://127.0.0.1:$FRONTDOOR_PORT/novnc/vnc.html\",\"status_code\":$NOVNC_HTTP_CODE,\"ok\":$([ \"$NOVNC_HTTP_CODE\" = "200" ] && echo true || echo false)}" >"$OUT_DIR/novnc_http_check.json"
evidence+=("{\"path\":\"artifacts/system/state_pack/$RUN_ID/novnc_http_check.json\",\"label\":\"novnc_http_check\"}")

# --- 5d. ws_probe (dual endpoints, tailnet, >=10s) ---
TS_HOSTNAME="${OPENCLAW_TS_HOSTNAME:-aiops-1.tailc75c62.ts.net}"
WS_PROBE_HOLD="${OPENCLAW_WS_PROBE_HOLD_SEC:-10}"
if [ -x "$SCRIPT_DIR/novnc_ws_probe.py" ]; then
  OPENCLAW_TS_HOSTNAME="$TS_HOSTNAME" OPENCLAW_WS_PROBE_HOLD_SEC="$WS_PROBE_HOLD" python3 "$SCRIPT_DIR/novnc_ws_probe.py" --host "$TS_HOSTNAME" --hold "$WS_PROBE_HOLD" --all 2>/dev/null >"$OUT_DIR/ws_probe.json" || echo '{"all_ok":false,"endpoints":{}}' >"$OUT_DIR/ws_probe.json"
else
  echo '{"all_ok":false,"endpoints":{},"reason":"novnc_ws_probe.py not found"}' >"$OUT_DIR/ws_probe.json"
fi
evidence+=("{\"path\":\"artifacts/system/state_pack/$RUN_ID/ws_probe.json\",\"label\":\"ws_probe\"}")

# --- 6. latest_runs_index ---
python3 -c "
import json, os
from pathlib import Path
root = Path('$ARTIFACTS_ROOT')
index = {'run_id': '$RUN_ID', 'projects': {}}
# deploy
deploy_dir = root / 'deploy'
if deploy_dir.exists():
    dirs = sorted([d.name for d in deploy_dir.iterdir() if d.is_dir()], reverse=True)[:5]
    for d in dirs:
        rj = deploy_dir / d / 'deploy_result.json'
        if rj.exists():
            try:
                data = json.loads(rj.read_text())
                index['projects']['deploy'] = {'run_id': d, 'status': data.get('overall'), 'artifact_dir': f'artifacts/deploy/{d}'}
                break
            except: pass
# soma_kajabi
sk = root / 'soma_kajabi'
for sub in ['auto_finish', 'run_to_done', 'capture_interactive']:
    p = sk / sub
    if p.exists():
        dirs = sorted([d.name for d in p.iterdir() if d.is_dir()], reverse=True)[:3]
        for d in dirs:
            index['projects'].setdefault('soma_kajabi', {})[sub] = {'run_id': d, 'artifact_dir': f'artifacts/soma_kajabi/{sub}/{d}'}
            break
with open('$OUT_DIR/latest_runs_index.json', 'w') as f:
    json.dump(index, f, indent=2)
" 2>/dev/null || echo '{"run_id":"'"$RUN_ID"'","projects":{}}' >"$OUT_DIR/latest_runs_index.json"
evidence+=("{\"path\":\"artifacts/system/state_pack/$RUN_ID/latest_runs_index.json\",\"label\":\"latest_runs_index\"}")

# --- 7. build_sha ---
BUILD_SHA=""
if [ -f "$ROOT_DIR/.git/HEAD" ]; then
  BUILD_SHA="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
fi
[ -z "$BUILD_SHA" ] && BUILD_SHA="unknown"
echo "$BUILD_SHA" >"$OUT_DIR/build_sha.txt"

# --- 8. drift summary (from /api/ui/version when available) ---
DRIFT_SUMMARY="unknown"
if curl -sf --connect-timeout 2 --max-time 4 "$CONSOLE_BASE/api/ui/version" >"$OUT_DIR/version.json" 2>/dev/null; then
  DRIFT_SUMMARY="$(python3 -c "
import json
try:
    d = json.load(open('$OUT_DIR/version.json'))
    status = d.get('drift_status', 'unknown')
    drift = d.get('drift')
    if status == 'unknown':
        print('unknown')
    elif drift is True:
        print('DRIFT: deployed != origin/main')
    elif drift is False:
        print('Up to date')
    else:
        print('unknown')
except: print('unknown')
" 2>/dev/null)" || DRIFT_SUMMARY="unknown"
fi

# --- 9. SUMMARY.md ---
cat >"$OUT_DIR/SUMMARY.md" <<EOF
# State Pack: $RUN_ID

**build_sha:** $BUILD_SHA
**drift_summary:** $DRIFT_SUMMARY
**Generated:** $(date -u +%Y-%m-%dT%H:%M:%SZ)

## Files
- health_public.json
- autopilot_status.json
- llm_status.json
- tailscale_serve.txt
- ports.txt
- systemd_*.txt
- latest_runs_index.json
- novnc_http_check.json
- ws_probe.json

## Checks
$(printf '%s\n' "${checks[@]}" | sed 's/^/- /')
EOF
evidence+=("{\"path\":\"artifacts/system/state_pack/$RUN_ID/SUMMARY.md\",\"label\":\"SUMMARY\"}")

# --- OCL Result (stdout) ---
fail_count=$(printf '%s\n' "${checks[@]}" | grep -c '"pass":false' || true)
if [ "$fail_count" -eq 0 ]; then
  status="ok"
elif [ "$fail_count" -lt "${#checks[@]}" ]; then
  status="partial"
else
  status="fail"
fi

checks_json="[$(IFS=,; echo "${checks[*]}")]"
evidence_json="[$(IFS=,; echo "${evidence[*]}")]"

cat <<EOF
{
  "status": "$status",
  "checks": $checks_json,
  "evidence": $evidence_json,
  "run_id": "$RUN_ID",
  "message": "State pack written to artifacts/system/state_pack/$RUN_ID",
  "artifact_dir": "artifacts/system/state_pack/$RUN_ID"
}
EOF

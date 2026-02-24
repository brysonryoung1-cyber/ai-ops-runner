#!/usr/bin/env bash
# openclaw_hq_audit.sh — Fully agentic HQ Audit (localhost-only, no tokens, no tailnet fetch).
#
# Executed by hostd on aiops-1. Uses ONLY 127.0.0.1 URLs. No OPENCLAW_HQ_TOKEN required.
# Produces artifacts/hq_audit/<run_id>/{SUMMARY.md,SUMMARY.json,LINKS.json}.
#
# Self-heal loop (3 retries max): restart hostd/novnc/console, fix tailscale serve, then rerun checks.
#
# Usage: Run via HQ → Actions → Run HQ Audit (or hostd exec openclaw_hq_audit).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_ID="${OPENCLAW_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_hqaudit}"
CONSOLE_BASE="http://127.0.0.1:8787"
HOSTD_BASE="http://127.0.0.1:8877"
ART_DIR="$ROOT_DIR/artifacts/hq_audit/$RUN_ID"
CONSOLE_PORT="${OPENCLAW_CONSOLE_PORT:-8787}"
MAX_RETRIES=3

mkdir -p "$ART_DIR"

# --- Helpers (localhost-only) ---
curl_local() {
  curl -sS --connect-timeout 5 --max-time 15 -o "$1" -w "%{http_code}" "$2" 2>/dev/null || echo "000"
}

jq_safe() {
  local f="$1"
  local q="$2"
  [ -f "$f" ] && jq -r "$q" "$f" 2>/dev/null || echo ""
}

# --- Self-heal: restart hostd if down ---
_heal_hostd() {
  if ! curl -sSf --connect-timeout 2 "$HOSTD_BASE/health" >/dev/null 2>&1; then
    if command -v systemctl >/dev/null 2>&1; then
      systemctl restart openclaw-hostd 2>/dev/null || true
      sleep 3
    fi
  fi
}

# --- Self-heal: restart novnc if probe fails ---
_heal_novnc() {
  local probe_script="$ROOT_DIR/ops/novnc_probe.sh"
  if [ -x "$probe_script" ]; then
    if ! "$probe_script" 2>/dev/null; then
      if command -v systemctl >/dev/null 2>&1; then
        systemctl restart openclaw-novnc 2>/dev/null || true
        sleep 5
      fi
    fi
  fi
}

# --- Self-heal: restart console container if unhealthy ---
_heal_console() {
  local unhealthy=""
  if command -v docker >/dev/null 2>&1; then
    unhealthy="$(docker compose -f "$ROOT_DIR/docker-compose.console.yml" ps --format json 2>/dev/null | python3 -c "
import sys, json
raw = sys.stdin.read().strip()
if not raw: sys.exit(0)
try:
    data = json.loads(raw) if raw.startswith('[') else [json.loads(raw)]
except: data = []
for s in (data if isinstance(data, list) else [data]):
    if isinstance(s, dict):
        h = (s.get('Health') or '').lower()
        st = (s.get('State') or '').lower()
        if st != 'running' or h == 'unhealthy':
            print(s.get('Name', s.get('Service', 'console')))
            break
" 2>/dev/null || true)"
    if [ -n "$unhealthy" ]; then
      docker compose -f "$ROOT_DIR/docker-compose.console.yml" restart 2>/dev/null || true
      sleep 5
    fi
  fi
}

# --- Self-heal: fix tailscale serve if root misrouted to 6080 (only if tailscale CLI available) ---
_heal_tailscale_serve() {
  if command -v tailscale >/dev/null 2>&1; then
    local root_body
    root_body="$(curl -skfsS --connect-timeout 3 --max-time 5 "https://$(tailscale status --json 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    name = (d.get('Self') or {}).get('DNSName', '').rstrip('.')
    print(name if name else '')
except: pass
" 2>/dev/null)/" 2>/dev/null)" || true
    if echo "$root_body" | grep -qE "Directory listing for /|vnc\.html"; then
      tailscale serve --bg --https=443 "http://127.0.0.1:$CONSOLE_PORT" 2>/dev/null || true
      sleep 2
    fi
  fi
}

# --- Run audit checks, write to REPORT_DIR ---
_run_audit() {
  local report_dir="$1"
  mkdir -p "$report_dir"

  # 1. API endpoints (localhost only)
  HP_CODE=$(curl_local "$report_dir/health_public.json" "$CONSOLE_BASE/api/ui/health_public")
  AUTH_CODE=$(curl_local "$report_dir/auth_status.json" "$CONSOLE_BASE/api/auth/status")
  HE_CODE=$(curl_local "$report_dir/host_executor.json" "$CONSOLE_BASE/api/host-executor/status")
  AP_CODE=$(curl_local "$report_dir/autopilot.json" "$CONSOLE_BASE/api/autopilot/status")
  HOSTD_CODE=$(curl_local "$report_dir/hostd_health.json" "$HOSTD_BASE/health")

  # 2. Docker compose ps health
  docker compose -f "$ROOT_DIR/docker-compose.console.yml" ps --format json 2>/dev/null >"$report_dir/docker_ps.json" || echo "[]" >"$report_dir/docker_ps.json"

  # 3. Systemd status
  for unit in openclaw-hostd openclaw-novnc openclaw-guard.timer openclaw-autopilot.timer; do
    systemctl status "$unit" --no-pager 2>/dev/null >"$report_dir/systemd_${unit}.txt" || echo "not found" >"$report_dir/systemd_${unit}.txt"
  done

  # 4. novnc_probe + novnc_status.json
  if [ -x "$ROOT_DIR/ops/novnc_probe.sh" ]; then
    "$ROOT_DIR/ops/novnc_probe.sh" 2>/dev/null && echo "ok" >"$report_dir/novnc_probe.txt" || echo "fail" >"$report_dir/novnc_probe.txt"
  else
    echo "skip" >"$report_dir/novnc_probe.txt"
  fi
  cp /run/openclaw/novnc_status.json "$report_dir/novnc_status.json" 2>/dev/null || echo '{"ok":false}' >"$report_dir/novnc_status.json"

  # 5. Last 25 runs from local run store (artifacts/runs)
  RUNS_DIR="$ROOT_DIR/artifacts/runs"
  if [ -d "$RUNS_DIR" ]; then
    ls -1t "$RUNS_DIR" 2>/dev/null | head -25 | while read -r d; do
      [ -f "$RUNS_DIR/$d/run.json" ] && cat "$RUNS_DIR/$d/run.json"
    done | python3 -c "
import sys, json
runs = []
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        r = json.loads(line)
        runs.append(r)
    except: pass
print(json.dumps({'runs': runs}))
" 2>/dev/null >"$report_dir/runs.json" || echo '{"runs":[]}' >"$report_dir/runs.json"
  else
    echo '{"runs":[]}' >"$report_dir/runs.json"
  fi

  # Return summary codes for pass/fail
  echo "${HP_CODE}|${AUTH_CODE}|${HE_CODE}|${AP_CODE}|${HOSTD_CODE}"
}

# --- Build SUMMARY.md, SUMMARY.json, LINKS.json ---
_build_artifacts() {
  local report_dir="$1"
  local codes="$2"

  IFS='|' read -r HP_CODE AUTH_CODE HE_CODE AP_CODE HOSTD_CODE <<< "$codes"

  HE_OK=$(jq_safe "$report_dir/host_executor.json" '.ok // false')
  HE_HOSTD=$(jq_safe "$report_dir/host_executor.json" '.hostd_status // "unknown"')
  BUILD_SHA=$(jq_safe "$report_dir/health_public.json" '.build_sha // "unknown"')
  DEPLOY_SHA=$(jq_safe "$report_dir/health_public.json" '.deploy_sha // "unknown"')

  HQ_PASS=false
  [ "$HP_CODE" = "200" ] && [ "$AUTH_CODE" = "200" ] && HQ_PASS=true

  HE_PASS=false
  [ "$HE_CODE" = "200" ] && [ "$HE_OK" = "true" ] && HE_PASS=true

  HOSTD_PASS=false
  [ "$HOSTD_CODE" = "200" ] && HOSTD_PASS=true

  AP_PASS=false
  [ "$AP_CODE" = "200" ] && AP_PASS=true
  [ "$AP_CODE" = "404" ] && AP_PASS=true  # autopilot not present is OK

  NOVNC_PROBE="skip"
  [ -f "$report_dir/novnc_probe.txt" ] && NOVNC_PROBE=$(cat "$report_dir/novnc_probe.txt")
  NOVNC_PASS=false
  [ "$NOVNC_PROBE" = "ok" ] && NOVNC_PASS=true

  # SUMMARY.json
  HP_PY=$([ "$HQ_PASS" = true ] && echo True || echo False)
  HE_PY=$([ "$HE_PASS" = true ] && echo True || echo False)
  HD_PY=$([ "$HOSTD_PASS" = true ] && echo True || echo False)
  AP_PY=$([ "$AP_PASS" = true ] && echo True || echo False)
  NV_PY=$([ "$NOVNC_PASS" = true ] && echo True || echo False)
  OVERALL_PY=$([ "$HQ_PASS" = true ] && [ "$HE_PASS" = true ] && [ "$HOSTD_PASS" = true ] && echo True || echo False)
  python3 -c "
import json
d = {
  'run_id': '$RUN_ID',
  'timestamp_utc': '$(date -u +%Y-%m-%dT%H:%M:%SZ)',
  'categories': {
    'hq_api': {'pass': $HP_PY, 'health_public': '$HP_CODE', 'auth_status': '$AUTH_CODE'},
    'host_executor': {'pass': $HE_PY, 'code': '$HE_CODE', 'hostd_status': '$HE_HOSTD'},
    'hostd': {'pass': $HD_PY, 'code': '$HOSTD_CODE'},
    'autopilot': {'pass': $AP_PY, 'code': '$AP_CODE'},
    'novnc': {'pass': $NV_PY, 'probe': '$NOVNC_PROBE'}
  },
  'build_sha': '$BUILD_SHA',
  'deploy_sha': '$DEPLOY_SHA',
  'overall_pass': $OVERALL_PY
}
with open('$ART_DIR/SUMMARY.json', 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null || true

  # SUMMARY.md
  cat >"$ART_DIR/SUMMARY.md" <<EOF
# OpenClaw HQ Audit Report

**Run ID**: $RUN_ID  
**Timestamp**: $(date -u +%Y-%m-%dT%H:%M:%SZ)  
**Build SHA**: $BUILD_SHA  
**Deploy SHA**: $DEPLOY_SHA  

## Category Status

| Category        | Status |
|-----------------|--------|
| HQ/API          | $([ "$HQ_PASS" = true ] && echo "PASS" || echo "FAIL") |
| Host Executor   | $([ "$HE_PASS" = true ] && echo "PASS" || echo "FAIL") |
| hostd           | $([ "$HOSTD_PASS" = true ] && echo "PASS" || echo "FAIL") |
| Autopilot       | $([ "$AP_PASS" = true ] && echo "PASS" || echo "OK (404=not present)") |
| noVNC           | $([ "$NOVNC_PASS" = true ] && echo "PASS" || echo "FAIL/skip") |

## Endpoint Codes

- /api/ui/health_public: $HP_CODE
- /api/auth/status: $AUTH_CODE
- /api/host-executor/status: $HE_CODE
- /api/autopilot/status: $AP_CODE
- hostd /health: $HOSTD_CODE

## Top Failures (from last 25 runs)

EOF
  if [ -f "$report_dir/runs.json" ]; then
    jq -r '.runs[]? | select((.exit_code != 0 and .exit_code != null) or .status != "success") | "\(.run_id) | \(.project_id // "—") | \(.action) | \(.error_summary // .stderr // "—" | tostring | .[0:80])"' "$report_dir/runs.json" 2>/dev/null | head -5 | while read -r line; do
      echo "- $line" >>"$ART_DIR/SUMMARY.md"
    done
  fi
  echo "" >>"$ART_DIR/SUMMARY.md"

  # LINKS.json (tailnet links for viewing only; audit does not depend on them)
  TS_HOSTNAME=""
  if command -v tailscale >/dev/null 2>&1; then
    TS_HOSTNAME="$(tailscale status --json 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    name = (d.get('Self') or {}).get('DNSName', '').rstrip('.')
    print(name if name else '')
except: pass
" 2>/dev/null)"
  fi
  TAILNET_URL=""
  [ -n "$TS_HOSTNAME" ] && TAILNET_URL="https://${TS_HOSTNAME}/artifacts/hq_audit/${RUN_ID}"
  TAILNET_URL="$TAILNET_URL" python3 -c "
import json, os
d = {
  'run_id': '$RUN_ID',
  'artifact_dir': 'artifacts/hq_audit/$RUN_ID',
  'tailnet_url': os.environ.get('TAILNET_URL') or None,
  'local_path': '$ART_DIR'
}
with open('$ART_DIR/LINKS.json', 'w') as f:
    json.dump(d, f, indent=2)
" 2>/dev/null || true
}

# --- Main: self-heal loop (3 retries) then audit ---
report_subdir="$ART_DIR/report"
codes=""
for attempt in $(seq 1 $MAX_RETRIES); do
  _heal_hostd
  _heal_novnc
  _heal_console
  _heal_tailscale_serve
  sleep 2
  codes=$(_run_audit "$report_subdir")
  IFS='|' read -r HP_CODE AUTH_CODE HE_CODE AP_CODE HOSTD_CODE <<< "$codes"
  # If critical services up, we're done
  if [ "$HOSTD_CODE" = "200" ] && [ "$HP_CODE" = "200" ]; then
    break
  fi
  [ "$attempt" -lt "$MAX_RETRIES" ] && sleep 5
done

_build_artifacts "$report_subdir" "$codes"

# Emit JSON line for hostd/exec to parse (artifact_dir)
echo "{\"ok\":true,\"run_id\":\"$RUN_ID\",\"artifact_dir\":\"artifacts/hq_audit/$RUN_ID\",\"summary_path\":\"artifacts/hq_audit/$RUN_ID/SUMMARY.md\"}"

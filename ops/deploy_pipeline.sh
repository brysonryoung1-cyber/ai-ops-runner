#!/usr/bin/env bash
# deploy_pipeline.sh — DEPLOY-ONLY pipeline for aiops-1 (pull + run). No git push, no ship.
#
# Runs ON the production host (aiops-1). Steps:
#   1. assert_production_pull_only (fail if push capable)
#   2. git fetch origin && git reset --hard origin/main
#   3. docker compose up -d --build (+ console if present)
#   4. ops/verify_production.sh
#   5. ops/dod_production.sh (Definition-of-Done; fail deploy if DoD fails)
#   6. ops/update_project_state.py (last_deploy_timestamp, last_verified_vps_head, doctor, guard)
#
# HARD GUARD: Script must not contain or invoke "git push" or "gh auth" for deploy steps.
# CONCURRENCY: flock on .locks/deploy.lock.
# ARTIFACTS: artifacts/deploy/<run_id>/{deploy_result.json, verify_production.json, dod_result pointer, doctor pointer}
# No secrets in any artifact.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# --- Run ID ---
RUN_ID="$(date -u +%Y%m%d_%H%M%S)-$(od -A n -t x4 -N 2 /dev/urandom 2>/dev/null | tr -d ' ' || echo "$$")"
DEPLOY_ARTIFACT_DIR="$ROOT_DIR/artifacts/deploy/$RUN_ID"
mkdir -p "$DEPLOY_ARTIFACT_DIR"

# --- Concurrency lock (flock when available, else mkdir) ---
LOCK_DIR="$ROOT_DIR/.locks"
mkdir -p "$LOCK_DIR"
LOCK_FILE="$LOCK_DIR/deploy.lock"
DEPLOY_LOCK_ACQUIRED=0
if command -v flock >/dev/null 2>&1; then
  exec 200>"$LOCK_FILE"
  if flock -n 200; then DEPLOY_LOCK_ACQUIRED=1; fi
else
  LOCK_MKDIR="${LOCK_FILE}.d"
  if mkdir "$LOCK_MKDIR" 2>/dev/null; then
    DEPLOY_LOCK_ACQUIRED=1
    trap 'rmdir "$LOCK_MKDIR" 2>/dev/null' EXIT
  fi
fi
if [ "$DEPLOY_LOCK_ACQUIRED" -eq 0 ]; then
  echo "ERROR: Another deploy is running (lock $LOCK_FILE). Aborting." >&2
  python3 -c "
import json
p = '$DEPLOY_ARTIFACT_DIR'
r = {'run_id': '$RUN_ID', 'overall': 'FAIL', 'step_failed': 'lock', 'error_class': 'deploy_lock_held', 'next_auto_fix': 'Wait for current deploy to finish', 'git_head': None, 'timestamps': {}}
with open(p + '/deploy_result.json', 'w') as f: json.dump(r, f, indent=2)
"
  exit 2
fi

# --- Hard guard: this script must not run push or gh auth (exclude comments and error messages) ---
GUARD_FAIL=0
VIOLATIONS=""
while IFS= read -r line; do
  echo "$line" | grep -q "git push\|gh auth" || continue
  echo "$line" | grep -q "^[[:space:]]*#" && continue
  echo "$line" | grep -q "must not\|forbid\|guard\|no push\|No git push\|GUARD_FAIL\|deploy_pipeline\|push_capability\|credentials\|script_contains_push\|write_fail\|error_class\|next_auto_fix\|grep -q\|grep -n\|VIOLATIONS" && continue
  VIOLATIONS="${VIOLATIONS}${line}"$'\n'
done < "$SCRIPT_DIR/deploy_pipeline.sh"
if [ -n "$VIOLATIONS" ]; then
  echo "ERROR: deploy_pipeline.sh must not contain 'git push' or 'gh auth' in deploy steps." >&2
  GUARD_FAIL=1
fi
if [ "$GUARD_FAIL" -eq 1 ]; then
  python3 -c "
import json
p = '$DEPLOY_ARTIFACT_DIR'
r = {'run_id': '$RUN_ID', 'overall': 'FAIL', 'step_failed': 'preflight', 'error_class': 'deploy_script_contains_push', 'next_auto_fix': 'Remove any push/gh auth from deploy_pipeline.sh', 'git_head': None, 'timestamps': {}}
with open(p + '/deploy_result.json', 'w') as f: json.dump(r, f, indent=2)
"
  exit 2
fi

write_fail() {
  local step="$1"
  local err_class="$2"
  local next_fix="$3"
  local log_ref="$4"
  GIT_HEAD="$(git rev-parse --short HEAD 2>/dev/null || echo null)"
  python3 -c "
import json, os
from datetime import datetime, timezone
p = os.environ.get('DEPLOY_ARTIFACT_DIR', '$DEPLOY_ARTIFACT_DIR')
r = {
  'run_id': '$RUN_ID',
  'overall': 'FAIL',
  'step_failed': '$step',
  'error_class': '$err_class',
  'next_auto_fix': '$next_fix',
  'git_head': '$GIT_HEAD',
  'timestamps': {'finished': datetime.now(timezone.utc).isoformat()},
  'artifacts': {'deploy_result': 'artifacts/deploy/$RUN_ID/deploy_result.json', 'verify_production': 'artifacts/deploy/$RUN_ID/verify_production.json', 'dod_result': None, 'log_ref': '$log_ref'}
}
with open(p + '/deploy_result.json', 'w') as f: json.dump(r, f, indent=2)
"
}

echo "=== deploy_pipeline.sh ==="
echo "  Run ID: $RUN_ID"
echo "  Artifacts: $DEPLOY_ARTIFACT_DIR"
echo ""

# --- Maintenance mode: prevent background doctor/timer during deploy ---
MAINTENANCE_FILE="$ROOT_DIR/artifacts/.maintenance_mode"
mkdir -p "$(dirname "$MAINTENANCE_FILE")"
echo "{\"maintenance_mode\": true, \"deploy_run_id\": \"$RUN_ID\"}" >"$MAINTENANCE_FILE"
echo "  Maintenance mode ON (deploy_run_id=$RUN_ID)"
if command -v systemctl >/dev/null 2>&1; then
  systemctl stop openclaw-doctor.timer 2>/dev/null || true
  echo "  Stopped openclaw-doctor.timer (if present)"
fi
export OPENCLAW_DEPLOY_RUN_ID="$RUN_ID"
echo ""

# --- Step 1: Assert production pull-only ---
STEP="assert_production_pull_only"
echo "==> Step 1: Assert production pull-only"
if ! "$SCRIPT_DIR/assert_production_pull_only.sh" >"$DEPLOY_ARTIFACT_DIR/assert_pull_only.log" 2>&1; then
  write_fail "$STEP" "push_capability_detected" "Ensure aiops-1 has no git push credentials or SSH write keys" "artifacts/deploy/$RUN_ID/assert_pull_only.log"
  exit 2
fi
echo "  Pull-only: PASS"
echo ""

# --- Step 2: git fetch + reset ---
STEP="git_sync"
echo "==> Step 2: git fetch origin && git reset --hard origin/main"
if ! git fetch origin main 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/git_fetch.log"; then
  write_fail "$STEP" "git_fetch_failed" "Check network and origin remote" "artifacts/deploy/$RUN_ID/git_fetch.log"
  exit 2
fi
git reset --hard origin/main 2>&1 | tee -a "$DEPLOY_ARTIFACT_DIR/git_fetch.log"
GIT_HEAD="$(git rev-parse --short HEAD)"
GIT_HEAD_FULL="$(git rev-parse HEAD)"
echo "  HEAD: $GIT_HEAD"
export OPENCLAW_BUILD_SHA="$GIT_HEAD"
echo ""

# --- Step 2b: Install hostd (idempotent) ---
echo "==> Step 2b: Install openclaw-hostd"
if [ -f "$SCRIPT_DIR/install_openclaw_hostd.sh" ]; then
  if ! "$SCRIPT_DIR/install_openclaw_hostd.sh" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/hostd_install.log"; then
    write_fail "hostd_install" "hostd_install_failed" "Fix install_openclaw_hostd.sh and re-run deploy" "artifacts/deploy/$RUN_ID/hostd_install.log"
    exit 2
  fi
else
  echo "  (install_openclaw_hostd.sh not found — skip)"
fi
echo ""

# --- Step 2c: Install Kajabi interactive capture deps (Xvfb, x11vnc, websockify) ---
echo "==> Step 2c: Install Kajabi interactive deps"
if [ -f "$SCRIPT_DIR/install_kajabi_interactive_deps.sh" ]; then
  if ! bash "$SCRIPT_DIR/install_kajabi_interactive_deps.sh" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/kajabi_deps_install.log"; then
    echo "  WARNING: Kajabi interactive deps failed (non-fatal; noVNC may be unavailable if Cloudflare blocks)" >&2
  else
    echo "  Kajabi interactive deps: PASS"
  fi
else
  echo "  (install_kajabi_interactive_deps.sh not found — skip)"
fi
echo ""

# --- Step 2c2: Install openclaw-novnc systemd unit (on-demand noVNC) ---
echo "==> Step 2c2: Install openclaw-novnc"
if [ -f "$SCRIPT_DIR/install_openclaw_novnc.sh" ]; then
  if ! bash "$SCRIPT_DIR/install_openclaw_novnc.sh" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/novnc_install.log"; then
    echo "  WARNING: openclaw-novnc install failed (non-fatal; capture may fall back to legacy mode)" >&2
  else
    echo "  openclaw-novnc: installed"
  fi
else
  echo "  (install_openclaw_novnc.sh not found — skip)"
fi
echo ""

# --- Step 2c2b: Install openclaw-frontdoor (Caddy reverse proxy for single-root Tailscale Serve) ---
echo "==> Step 2c2b: Install openclaw-frontdoor"
if [ -f "$SCRIPT_DIR/install_openclaw_frontdoor.sh" ]; then
  if ! sudo bash "$SCRIPT_DIR/install_openclaw_frontdoor.sh" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/frontdoor_install.log"; then
    echo "  WARNING: openclaw-frontdoor install failed (non-fatal; serve_guard falls back to per-path)" >&2
  else
    echo "  openclaw-frontdoor: installed"
  fi
else
  echo "  (install_openclaw_frontdoor.sh not found — skip)"
fi
echo ""

# --- Step 2c3: Install Soma Kajabi Session Warm timer (disabled by default) ---
echo "==> Step 2c3: Install Soma Kajabi Session Warm timer"
if [ -f "$SCRIPT_DIR/install_openclaw_soma_kajabi_warm.sh" ]; then
  if ! bash "$SCRIPT_DIR/install_openclaw_soma_kajabi_warm.sh" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/soma_kajabi_warm_install.log"; then
    echo "  WARNING: soma-kajabi-warm install failed (non-fatal)" >&2
  else
    echo "  soma-kajabi-warm: installed (disabled by default)"
  fi
else
  echo "  (install_openclaw_soma_kajabi_warm.sh not found — skip)"
fi
echo ""

# --- Step 2c4: Install Soma Autopilot timer (every 10 min; enabled via config flag) ---
echo "==> Step 2c4: Install Soma Autopilot timer"
if [ -f "$SCRIPT_DIR/openclaw_install_soma_autopilot.sh" ]; then
  if ! bash "$SCRIPT_DIR/openclaw_install_soma_autopilot.sh" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/soma_autopilot_install.log"; then
    echo "  WARNING: soma-autopilot install failed (non-fatal)" >&2
  else
    echo "  soma-autopilot: installed (enable via touch /etc/ai-ops-runner/config/soma_autopilot_enabled.txt)"
  fi
else
  echo "  (openclaw_install_soma_autopilot.sh not found — skip)"
fi
echo ""

# --- Step 2c4b: Install openclaw-reconcile timer (idempotent) ---
echo "==> Step 2c4b: Install openclaw-reconcile timer"
if [ -f "$SCRIPT_DIR/openclaw_install_reconcile.sh" ]; then
  if ! sudo bash "$SCRIPT_DIR/openclaw_install_reconcile.sh" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/reconcile_install.log"; then
    echo "  WARNING: reconcile timer install failed (non-fatal)" >&2
  else
    echo "  openclaw-reconcile: installed (every 5-10 min)"
  fi
else
  echo "  (openclaw_install_reconcile.sh not found — skip)"
fi
echo ""

# --- Step 2d: noVNC firewall (port 6080 Tailscale-only) ---
echo "==> Step 2d: noVNC firewall (Tailscale-only)"
if [ -f "$SCRIPT_DIR/ufw_novnc_tailscale_only.sh" ]; then
  if sudo "$SCRIPT_DIR/ufw_novnc_tailscale_only.sh" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/ufw_novnc.log"; then
    echo "  noVNC firewall: PASS"
  else
    echo "  WARNING: ufw_novnc_tailscale_only.sh failed (non-fatal; noVNC may still work if ufw allows tailscale0)" >&2
  fi
else
  echo "  (ufw_novnc_tailscale_only.sh not found — skip)"
fi
echo ""

# --- Step 3a: Explicit console build (fail-closed; no || true bypass) ---
if [ -f "docker-compose.console.yml" ]; then
  STEP="console_build"
  echo "==> Step 3a: Build openclaw_console image"
  CONSOLE_TOKEN=""
  [ -f /etc/ai-ops-runner/secrets/openclaw_console_token ] && CONSOLE_TOKEN="$(cat /etc/ai-ops-runner/secrets/openclaw_console_token 2>/dev/null | tr -d '[:space:]')"
  export OPENCLAW_CONSOLE_TOKEN="$CONSOLE_TOKEN"
  ADMIN_TOKEN=""
  for f in /etc/ai-ops-runner/secrets/openclaw_admin_token /etc/ai-ops-runner/secrets/openclaw_console_token /etc/ai-ops-runner/secrets/openclaw_api_token /etc/ai-ops-runner/secrets/openclaw_token; do
    [ -f "$f" ] && ADMIN_TOKEN="$(cat "$f" 2>/dev/null | tr -d '[:space:]')" && [ -n "$ADMIN_TOKEN" ] && break
  done
  export OPENCLAW_ADMIN_TOKEN="${ADMIN_TOKEN}"
  AIOPS_HOST="$(tailscale ip -4 2>/dev/null | head -n1 | tr -d '[:space:]')"
  [ -n "$AIOPS_HOST" ] && export AIOPS_HOST
  if ! OPENCLAW_BUILD_SHA="$GIT_HEAD" docker compose -f docker-compose.yml -f docker-compose.console.yml build --build-arg OPENCLAW_BUILD_SHA="$GIT_HEAD" openclaw_console 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/console_build.log"; then
    echo "console_build_failed" >"$DEPLOY_ARTIFACT_DIR/console_build_failed"
    # --- Diagnostic probe: capture toolchain info from the builder base image ---
    PROBE_IMG="$(grep -m1 'FROM node:' apps/openclaw-console/Dockerfile | awk '{print $2}' 2>/dev/null || echo 'node:20-alpine')"
    docker run --rm "$PROBE_IMG" sh -c 'echo "node: $(node -v 2>/dev/null || echo MISSING)"; echo "npm: $(npm -v 2>/dev/null || echo MISSING)"; echo "python3: $(python3 --version 2>/dev/null || echo MISSING)"; echo "which node: $(which node 2>/dev/null || echo MISSING)"; echo "which npm: $(which npm 2>/dev/null || echo MISSING)"' >"$DEPLOY_ARTIFACT_DIR/console_build_probe.txt" 2>&1 || true
    # --- JSON summary of failure (reads log file directly to avoid quoting issues) ---
    CONSOLE_BUILD_FAIL_IMG="$PROBE_IMG" python3 -c "
import json, os
p = os.environ.get('DEPLOY_ARTIFACT_DIR', '')
probe = ''
probe_path = os.path.join(p, 'console_build_probe.txt')
if os.path.isfile(probe_path):
    with open(probe_path) as f: probe = f.read().strip()
tail_lines = []
log_path = os.path.join(p, 'console_build.log')
if os.path.isfile(log_path):
    with open(log_path) as f: tail_lines = f.read().strip().split('\n')[-40:]
summary = {
    'error_class': 'console_build_failed',
    'exit_code': 1,
    'base_image': os.environ.get('CONSOLE_BUILD_FAIL_IMG', 'unknown'),
    'probe': probe,
    'tail_40_lines': tail_lines
}
with open(os.path.join(p, 'console_build_fail.json'), 'w') as f:
    json.dump(summary, f, indent=2)
" 2>/dev/null || true
    write_fail "$STEP" "console_build_failed" "Fix openclaw-console build/typecheck and re-run deploy" "artifacts/deploy/$RUN_ID/console_build.log"
    exit 2
  fi
  echo "  Console build: PASS"
  echo ""
fi

# --- Step 3b: Docker compose ---
STEP="docker_compose"
echo "==> Step 3b: docker compose up -d --build (OPENCLAW_BUILD_SHA=$OPENCLAW_BUILD_SHA)"
export OPENCLAW_BUILD_SHA
if ! OPENCLAW_BUILD_SHA="$GIT_HEAD" docker compose up -d --build 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/docker.log"; then
  write_fail "$STEP" "docker_compose_failed" "Fix Docker build/run and re-run deploy" "artifacts/deploy/$RUN_ID/docker.log"
  exit 2
fi
# Console stack if present (image already built above; no bypass)
if [ -f "docker-compose.console.yml" ]; then
  if ! OPENCLAW_BUILD_SHA="$GIT_HEAD" docker compose -f docker-compose.yml -f docker-compose.console.yml up -d --build 2>&1 | tee -a "$DEPLOY_ARTIFACT_DIR/docker.log"; then
    write_fail "console_compose" "console_compose_failed" "Fix console compose and re-run deploy" "artifacts/deploy/$RUN_ID/docker.log"
    exit 2
  fi
fi
echo "  Docker: done"
echo ""

# --- Step 4: Update project state with deploy timestamp (so verify can require non-null) ---
STEP="update_project_state"
echo "==> Step 4: Update project state (deploy timestamp)"
export OPENCLAW_DEPLOY_TIMESTAMP="$RUN_ID"
if [ -f "$SCRIPT_DIR/update_project_state.py" ]; then
  OPS_DIR="$SCRIPT_DIR" python3 "$SCRIPT_DIR/update_project_state.py" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/update_state.log" || {
    write_fail "$STEP" "update_state_failed" "Fix update_project_state.py and re-run" "artifacts/deploy/$RUN_ID/update_state.log"
    exit 2
  }
else
  write_fail "$STEP" "update_state_missing" "Restore ops/update_project_state.py" "artifacts/deploy/$RUN_ID/update_state.log"
  exit 2
fi
echo "  Deploy timestamp set"
echo ""

# --- Step 5: verify_production ---
STEP="verify_production"
echo "==> Step 5: Verify production"
export SHIP_ARTIFACT_DIR="$DEPLOY_ARTIFACT_DIR"
if ! "$SCRIPT_DIR/verify_production.sh" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/verify.log"; then
  write_fail "$STEP" "verification_failed" "Fix health/endpoints and re-run verify_production" "artifacts/deploy/$RUN_ID/verify.log"
  exit 2
fi
echo "  Verify: PASS"
echo ""

# --- Step 5a: Console route gate (/api/dod/last must exist; no 404) ---
STEP="console_route_gate"
echo "==> Step 5a: Console route gate (/api/ai-status, /api/dod/last)"
ROUTE_BASE="${OPENCLAW_VERIFY_BASE_URL:-http://127.0.0.1:8787}"
AI_STATUS_BODY="$(curl -sf --connect-timeout 5 --max-time 10 "$ROUTE_BASE/api/ai-status" 2>/dev/null)" || true
if [ -z "$AI_STATUS_BODY" ]; then
  write_fail "$STEP" "console_ai_status_unreachable" "Console /api/ai-status unreachable; fix console service and re-run deploy" "artifacts/deploy/$RUN_ID/verify.log"
  exit 2
fi
if ! echo "$AI_STATUS_BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('ok') is True else 1)" 2>/dev/null; then
  write_fail "$STEP" "console_ai_status_not_ok" "Console /api/ai-status ok != true; fix console health and re-run deploy" "artifacts/deploy/$RUN_ID/verify.log"
  exit 2
fi
DOD_HTTP="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 "$ROUTE_BASE/api/dod/last" 2>/dev/null)" || DOD_HTTP="000"
if [ "$DOD_HTTP" != "200" ]; then
  echo "missing_route_dod_last" >"$DEPLOY_ARTIFACT_DIR/missing_route_dod_last"
  write_fail "$STEP" "missing_route_dod_last" "GET /api/dod/last returned $DOD_HTTP (expect 200); console route missing or broken" "artifacts/deploy/$RUN_ID/verify.log"
  exit 2
fi
echo "  Console route gate: PASS (/api/ai-status ok:true, /api/dod/last 200)"
echo ""

# --- Step 5a2: build_sha consistency (deploy proof) ---
STEP="build_sha_verify"
echo "==> Step 5a2: Verify health_public.build_sha == git HEAD"
HEALTH_JSON="$(curl -sf --connect-timeout 5 --max-time 10 "$ROUTE_BASE/api/ui/health_public" 2>/dev/null)" || true
if [ -z "$HEALTH_JSON" ]; then
  write_fail "$STEP" "health_public_unreachable" "Console /api/ui/health_public unreachable" "artifacts/deploy/$RUN_ID/verify.log"
  exit 2
fi
BUILD_SHA="$(echo "$HEALTH_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('build_sha',''))" 2>/dev/null)" || BUILD_SHA=""
if [ "$BUILD_SHA" != "$GIT_HEAD" ]; then
  echo "  FAIL: build_sha=$BUILD_SHA != GIT_HEAD=$GIT_HEAD (console image not rebuilt)" >&2
  write_fail "$STEP" "build_sha_mismatch" "Console build_sha ($BUILD_SHA) != deploy SHA ($GIT_HEAD). Rebuild console: docker compose -f docker-compose.yml -f docker-compose.console.yml build --no-cache openclaw_console" "artifacts/deploy/$RUN_ID/verify.log"
  exit 2
fi
echo "  build_sha == deploy_sha == $GIT_HEAD: PASS"
echo ""

# --- Step 5b: Definition-of-Done (executable DoD; fail deploy if any check fails) ---
STEP="dod_production"
echo "==> Step 5b: Definition-of-Done (dod_production.sh)"
export SHIP_ARTIFACT_DIR="$DEPLOY_ARTIFACT_DIR"
export OPENCLAW_VERIFY_BASE_URL="${OPENCLAW_VERIFY_BASE_URL:-http://127.0.0.1:8787}"
if ! "$SCRIPT_DIR/dod_production.sh" 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/dod.log"; then
  write_fail "$STEP" "dod_failed" "Fix DoD checks (hostd, endpoints, doctor, artifacts, hard-fail strings) and re-run deploy" "artifacts/deploy/$RUN_ID/dod.log"
  exit 2
fi
# Copy or link latest dod result into deploy artifact dir for /api/deploy/last
DOD_LATEST=""
for d in $(ls -1dt "$ROOT_DIR/artifacts/dod"/[0-9]* 2>/dev/null | head -1); do
  [ -f "$d/dod_result.json" ] && DOD_LATEST="artifacts/dod/$(basename "$d")/dod_result.json" && break
done
echo "  DoD: PASS (dod_result: ${DOD_LATEST:-—})"
echo ""

# --- Step 6: update_project_state again (doctor/guard from this run) ---
STEP="update_project_state_refresh"
echo "==> Step 6: Refresh project state (canonical brain)"
if [ -f "$SCRIPT_DIR/update_project_state.py" ]; then
  OPS_DIR="$SCRIPT_DIR" python3 "$SCRIPT_DIR/update_project_state.py" 2>&1 | tee -a "$DEPLOY_ARTIFACT_DIR/update_state.log" || true
fi
echo "  Project state refreshed"
echo ""

# --- Doctor/guard and DoD artifact pointers (latest) ---
DOCTOR_POINTER=""
for d in $(ls -1dt "$ROOT_DIR/artifacts/doctor"/[0-9]* 2>/dev/null | head -1); do
  [ -f "$d/doctor.json" ] && DOCTOR_POINTER="artifacts/doctor/$(basename "$d")/doctor.json" && break
done
DOD_POINTER=""
for d in $(ls -1dt "$ROOT_DIR/artifacts/dod"/[0-9]* 2>/dev/null | head -1); do
  [ -f "$d/dod_result.json" ] && DOD_POINTER="artifacts/dod/$(basename "$d")/dod_result.json" && break
done

# --- Clear maintenance mode and restart doctor timer (deploy success) ---
rm -f "$ROOT_DIR/artifacts/.maintenance_mode"
echo "  Maintenance mode OFF"
if command -v systemctl >/dev/null 2>&1; then
  systemctl start openclaw-doctor.timer 2>/dev/null || true
  echo "  Started openclaw-doctor.timer (if present)"
fi

# --- Autopilot: install (idempotent), enable, and run one tick (deploy success only) ---
if [ -f "$SCRIPT_DIR/openclaw_install_autopilot.sh" ]; then
  echo "==> Autopilot: enable + run-now"
  if "$SCRIPT_DIR/openclaw_install_autopilot.sh" --enable --run-now 2>&1 | tee "$DEPLOY_ARTIFACT_DIR/autopilot_enable.log"; then
    echo "  Autopilot: enabled and run-now complete"
  else
    echo "  WARNING: Autopilot enable/run-now failed (non-fatal; deploy succeeded)" >&2
  fi
fi
echo ""

# --- Success artifact ---
python3 -c "
import json
from datetime import datetime, timezone
p = '$DEPLOY_ARTIFACT_DIR'
r = {
  'run_id': '$RUN_ID',
  'overall': 'PASS',
  'step_failed': None,
  'error_class': None,
  'next_auto_fix': None,
  'git_head': '$GIT_HEAD',
  'timestamps': {'finished': datetime.now(timezone.utc).isoformat()},
  'artifacts': {
    'deploy_result': 'artifacts/deploy/$RUN_ID/deploy_result.json',
    'verify_production': 'artifacts/deploy/$RUN_ID/verify_production.json',
    'dod_result': '$DOD_POINTER',
    'doctor': '$DOCTOR_POINTER'
  }
}
with open(p + '/deploy_result.json', 'w') as f: json.dump(r, f, indent=2)
"

# --- Deploy receipt (consumed by /api/ui/health_public for deploy_sha) ---
python3 -c "
import json
from datetime import datetime, timezone
receipt = {
  'deploy_sha': '$GIT_HEAD',
  'vps_head': '$GIT_HEAD',
  'console_build_sha': '$GIT_HEAD',
  'run_id': '$RUN_ID',
  'deployed_at': datetime.now(timezone.utc).isoformat(),
}
with open('$DEPLOY_ARTIFACT_DIR/deploy_receipt.json', 'w') as f:
    json.dump(receipt, f, indent=2)
"

# --- deploy_info.json for /api/ui/version (drift-proof) ---
python3 -c "
import json
import subprocess
from datetime import datetime, timezone
tree_sha = None
try:
    out = subprocess.run(['git', 'rev-parse', 'HEAD^{tree}'], capture_output=True, text=True, timeout=5, cwd='$ROOT_DIR')
    if out.returncode == 0 and out.stdout:
        tree_sha = out.stdout.strip()[:40]
except: pass
info = {
  'deploy_sha': '$GIT_HEAD',
  'deployed_head_sha': '$GIT_HEAD',
  'deployed_tree_sha': tree_sha,
  'run_id': '$RUN_ID',
  'last_deploy_time': datetime.now(timezone.utc).isoformat(),
  'deployed_at': datetime.now(timezone.utc).isoformat(),
}
with open('$DEPLOY_ARTIFACT_DIR/deploy_info.json', 'w') as f:
    json.dump(info, f, indent=2)
"
# Copy to /etc/ai-ops-runner for /api/ui/version (idempotent)
sudo mkdir -p /etc/ai-ops-runner
sudo cp \"$DEPLOY_ARTIFACT_DIR/deploy_info.json\" /etc/ai-ops-runner/deploy_info.json 2>/dev/null || true

echo "=== deploy_pipeline.sh COMPLETE ==="
echo "  Run ID: $RUN_ID"
echo "  Result: PASS"
echo "  Artifacts: artifacts/deploy/$RUN_ID/"
exit 0

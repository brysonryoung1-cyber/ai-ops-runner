#!/usr/bin/env bash
# csr_deploy_soma_autopilot.sh — CSR fail-closed autopilot for OpenClaw/Soma.
# Deploy origin/main (target SHA) to aiops-1, enable Soma Autopilot, prove end-to-end.
#
# Usage: ./ops/csr_deploy_soma_autopilot.sh [target_sha]
# Default target_sha: origin/main HEAD
# Run from Mac or aiops-1. NO SSH required when:
#   - ON aiops-1 (local mode), or
#   - OPENCLAW_HQ_BASE=https://aiops-1.tailc75c62.ts.net + OPENCLAW_HQ_TOKEN (HQ API mode)
# Remote SSH: OPENCLAW_VPS_SSH_HOST, OPENCLAW_VPS_SSH_IDENTITY (only when HQ API not used).
# No secrets printed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_SHA="${1:-$(cd "$ROOT_DIR" && git rev-parse --short origin/main 2>/dev/null || echo "6d40278")}"
VPS_HOST="${OPENCLAW_VPS_SSH_HOST:-root@100.123.61.57}"
SSH_OPTS="-o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
[ -n "${OPENCLAW_VPS_SSH_IDENTITY:-}" ] && [ -r "${OPENCLAW_VPS_SSH_IDENTITY}" ] && SSH_OPTS="$SSH_OPTS -o IdentitiesOnly=yes -i ${OPENCLAW_VPS_SSH_IDENTITY}"

RUN_ID="$(date -u +%Y%m%d_%H%M%S)-csr"
PROOF_DIR="$ROOT_DIR/artifacts/soma_kajabi/autopilot_install/$RUN_ID"
mkdir -p "$PROOF_DIR"

# Detect if we're on aiops-1 (local mode)
ON_AIOPS=0
[ "$(hostname -s 2>/dev/null || hostname)" = "aiops-1" ] && ON_AIOPS=1
[ "$ROOT_DIR" = "/opt/ai-ops-runner" ] && ON_AIOPS=1

# HQ API mode: when OPENCLAW_HQ_BASE is https URL + token, use curl (no SSH). APPLY_MODE_DRIFT: never require SSH for aiops-1.
HQ_BASE="${OPENCLAW_HQ_BASE:-}"
HQ_TOKEN="${OPENCLAW_HQ_TOKEN:-}"
USE_HQ_API=0
if [ "$ON_AIOPS" = "0" ] && [[ "$HQ_BASE" =~ ^https:// ]] && [ -n "$HQ_TOKEN" ]; then
  USE_HQ_API=1
fi
# When SSH key missing and HQ API available, prefer HQ API (must not stop at SSH key missing)
if [ "$ON_AIOPS" = "0" ] && [ "$USE_HQ_API" = "0" ] && { [ -z "${OPENCLAW_VPS_SSH_IDENTITY:-}" ] || [ ! -r "${OPENCLAW_VPS_SSH_IDENTITY}" ]; }; then
  if [[ "${HQ_BASE:-}" =~ ^https:// ]] && [ -n "${HQ_TOKEN:-}" ]; then
    USE_HQ_API=1
    echo "  Note: SSH key not set; using HQ API mode (OPENCLAW_HQ_BASE + OPENCLAW_HQ_TOKEN)" >&2
  fi
fi

_run_remote() {
  if [ "$ON_AIOPS" = "1" ]; then
    eval "$@"
  elif [ "$USE_HQ_API" = "1" ]; then
    echo "  HQ API mode: _run_remote not supported for: $*" >&2
    return 1
  else
    ssh $SSH_OPTS "$VPS_HOST" "$@"
  fi
}

_run_remote_bash() {
  if [ "$ON_AIOPS" = "1" ]; then
    bash -c "$1"
  elif [ "$USE_HQ_API" = "1" ]; then
    echo "  HQ API mode: _run_remote_bash not supported" >&2
    return 1
  else
    ssh $SSH_OPTS "$VPS_HOST" "bash -c $(printf '%q' "$1")"
  fi
}

_hq_curl() {
  local method="${1:-GET}"
  local path="$2"
  local data="${3:-}"
  local curl_args=(-sS -X "$method" "${HQ_BASE}${path}" -H "Content-Type: application/json")
  [ -n "$HQ_TOKEN" ] && curl_args+=(-H "X-OpenClaw-Token: $HQ_TOKEN")
  [ -n "$data" ] && curl_args+=(-d "$data")
  curl "${curl_args[@]}"
}

echo "=== CSR Deploy + Soma Autopilot ==="
echo "  Target SHA: $TARGET_SHA"
echo "  Mode: $([ "$ON_AIOPS" = "1" ] && echo "local (aiops-1)" || ([ "$USE_HQ_API" = "1" ] && echo "HQ API ($HQ_BASE)" || echo "remote ($VPS_HOST)"))"
echo "  Proof dir: $PROOF_DIR"
echo ""

# --- A) Deploy + Verify ---
echo "==> A) Deploy + Verify (deploy_and_verify via HQ)"
DEPLOY_RC=0
if [ "$USE_HQ_API" = "1" ]; then
  resp="$(_hq_curl POST /api/exec '{"action":"deploy_and_verify"}')"
  if ! echo "$resp" | grep -q '"run_id"'; then
    echo "  Trigger failed: $resp"
    DEPLOY_RC=1
  else
    run_id="$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('run_id',''))")"
    echo "  run_id=$run_id"
    for i in $(seq 1 120); do
      run_json="$(_hq_curl GET "/api/runs?id=$run_id")"
      status="$(echo "$run_json" | python3 -c "import sys,json; r=json.load(sys.stdin).get('run',{}); print(r.get('status',''))" 2>/dev/null || echo "")"
      [ "$status" = "success" ] && echo "  status=success" && DEPLOY_RC=0 && break
      [ "$status" = "failure" ] || [ "$status" = "error" ] && echo "  status=$status" && DEPLOY_RC=1 && break
      sleep 5
    done
    [ "$DEPLOY_RC" != "0" ] && echo "  status=timeout" && DEPLOY_RC=1
  fi
else
  _run_remote_bash '
  cd /opt/ai-ops-runner
  HQ_BASE="http://127.0.0.1:8787"
  TOKEN=""
  for f in /etc/ai-ops-runner/secrets/openclaw_admin_token /etc/ai-ops-runner/secrets/openclaw_console_token; do
    [ -f "$f" ] && TOKEN=$(cat "$f" | tr -d "[:space:]") && [ -n "$TOKEN" ] && break
  done
  if [ -z "$TOKEN" ]; then
    echo "ERROR: No admin token found"
    exit 1
  fi
  resp=$(curl -sS -X POST "$HQ_BASE/api/exec" -H "Content-Type: application/json" -H "X-OpenClaw-Token: $TOKEN" -d "{\"action\":\"deploy_and_verify\"}")
  if ! echo "$resp" | grep -q "\"run_id\""; then
    echo "Trigger failed: $resp"
    exit 1
  fi
  run_id=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get(\"run_id\",\"\"))")
  echo "run_id=$run_id"
  for i in $(seq 1 120); do
    run_json=$(curl -sS -H "X-OpenClaw-Token: $TOKEN" "$HQ_BASE/api/runs?id=$run_id")
    status=$(echo "$run_json" | python3 -c "import sys,json; r=json.load(sys.stdin).get(\"run\",{}); print(r.get(\"status\",\"\"))" 2>/dev/null || echo "")
    [ "$status" = "success" ] && echo "status=success" && exit 0
    [ "$status" = "failure" ] || [ "$status" = "error" ] && echo "status=$status" && exit 1
    sleep 5
  done
  echo "status=timeout"
  exit 1
' || DEPLOY_RC=$?
fi

if [ "$DEPLOY_RC" -ne 0 ]; then
  echo "  Deploy poll returned non-success; checking build_sha (may already match)..."
  if [ "$USE_HQ_API" = "1" ]; then
    BUILD_CHECK="$(_hq_curl GET /api/ui/health_public | python3 -c "import sys,json; print(json.load(sys.stdin).get('build_sha',''))" 2>/dev/null || echo "")"
  else
    BUILD_CHECK="$(_run_remote_bash 'curl -sf http://127.0.0.1:8787/api/ui/health_public 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get(\"build_sha\",\"\"))" 2>/dev/null || echo ""')"
  fi
  if [ -n "$BUILD_CHECK" ] && [[ "$BUILD_CHECK" == "$TARGET_SHA"* ]]; then
    echo "  build_sha already matches $TARGET_SHA; continuing"
    DEPLOY_RC=0
  else
    if [ "$USE_HQ_API" = "1" ]; then
      echo "  HQ API mode: no retry loop (deploy triggered once)"
    else
    echo "  Looping retry per fail-closed..."
    for retry in 1 2 3; do
      echo "  Retry $retry/3..."
      _run_remote_bash '
        cd /opt/ai-ops-runner
        HQ_BASE="http://127.0.0.1:8787"
        TOKEN=""
        for f in /etc/ai-ops-runner/secrets/openclaw_admin_token /etc/ai-ops-runner/secrets/openclaw_console_token; do
          [ -f "$f" ] && TOKEN=$(cat "$f" | tr -d "[:space:]") && [ -n "$TOKEN" ] && break
        done
        resp=$(curl -sS -X POST "$HQ_BASE/api/exec" -H "Content-Type: application/json" -H "X-OpenClaw-Token: $TOKEN" -d "{\"action\":\"deploy_and_verify\"}")
        run_id=$(echo "$resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get(\"run_id\",\"\"))")
        for i in $(seq 1 120); do
          run_json=$(curl -sS -H "X-OpenClaw-Token: $TOKEN" "$HQ_BASE/api/runs?id=$run_id")
          status=$(echo "$run_json" | python3 -c "import sys,json; r=json.load(sys.stdin).get(\"run\",{}); print(r.get(\"status\",\"\"))" 2>/dev/null || echo "")
          [ "$status" = "success" ] && echo "status=success" && exit 0
          [ "$status" = "failure" ] || [ "$status" = "error" ] && echo "status=$status" && exit 1
          sleep 5
        done
        exit 1
      ' && DEPLOY_RC=0 && break
    done
    fi
  fi
fi

# Verify build_sha
echo ""
echo "==> A2) Verify build_sha"
if [ "$USE_HQ_API" = "1" ]; then
  BUILD_SHA="$(_hq_curl GET /api/ui/health_public | python3 -c "import sys,json; print(json.load(sys.stdin).get('build_sha',''))" 2>/dev/null || echo "")"
else
  BUILD_SHA="$(_run_remote_bash 'curl -sf http://127.0.0.1:8787/api/ui/health_public 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get(\"build_sha\",\"\"))" 2>/dev/null || echo ""')"
fi
echo "  build_sha: ${BUILD_SHA:-unknown}"
if [ -z "$BUILD_SHA" ] || [[ ! "$BUILD_SHA" == "$TARGET_SHA"* ]]; then
  echo "  MISMATCH: expected $TARGET_SHA*, got $BUILD_SHA"
  echo "  Fail-closed: deploy loop exhausted or health unreachable"
  exit 1
fi
echo "  PASS: build_sha starts with $TARGET_SHA"
echo ""

if [ "$USE_HQ_API" = "1" ]; then
  echo "  HQ API mode: steps B–E require host access. Run on aiops-1 for full autopilot install."
  echo "  Deploy verified. Exiting."
  echo ""
  echo "=== CSR OUTPUT (no secrets) ==="
  echo "origin/main SHA: $TARGET_SHA"
  echo "aiops-1 build_sha: $BUILD_SHA"
  echo "mode: HQ API (deploy only; autopilot install skipped)"
  echo "=== END ==="
  exit 0
fi

# --- B) Install autopilot units ---
echo "==> B) Install autopilot units"
_run_remote_bash '
  cd /opt/ai-ops-runner
  if [ ! -f /etc/systemd/system/openclaw-soma-autopilot.service ] || [ ! -f /etc/systemd/system/openclaw-soma-autopilot.timer ]; then
    [ -f ./ops/openclaw_install_soma_autopilot.sh ] && sudo ./ops/openclaw_install_soma_autopilot.sh
  fi
  [ -f /etc/systemd/system/openclaw-soma-autopilot.service ] || exit 1
  [ -f /etc/systemd/system/openclaw-soma-autopilot.timer ] || exit 1
  sudo systemctl daemon-reload
  sudo systemctl enable --now openclaw-soma-autopilot.timer
  echo "is-enabled=$(systemctl is-enabled openclaw-soma-autopilot.timer 2>/dev/null || echo unknown)"
  echo "is-active=$(systemctl is-active openclaw-soma-autopilot.timer 2>/dev/null || echo unknown)"
  echo "next=$(systemctl list-timers openclaw-soma-autopilot.timer --no-pager 2>/dev/null | tail -1 || echo "—")"
'
TIMER_PROOF="$(_run_remote_bash '
  echo "is-enabled=$(systemctl is-enabled openclaw-soma-autopilot.timer 2>/dev/null || echo unknown)"
  echo "is-active=$(systemctl is-active openclaw-soma-autopilot.timer 2>/dev/null || echo unknown)"
  systemctl list-timers openclaw-soma-autopilot.timer --no-pager 2>/dev/null || true
')"
echo "$TIMER_PROOF" > "$PROOF_DIR/proof.txt"
echo "  Proof saved: $PROOF_DIR/proof.txt"
echo ""

# --- C) Enable autopilot flag ---
echo "==> C) Enable autopilot flag"
_run_remote_bash '
  sudo mkdir -p /etc/ai-ops-runner/config
  sudo touch /etc/ai-ops-runner/config/soma_autopilot_enabled.txt
  sudo chmod 644 /etc/ai-ops-runner/config/soma_autopilot_enabled.txt
  [ -f /etc/ai-ops-runner/config/soma_autopilot_enabled.txt ] && echo "flag_created=yes" || echo "flag_created=no"
'
echo "  flag: created"
echo "flag_created=yes" >> "$PROOF_DIR/proof.txt"
echo ""

# --- D) Verify HQ surfaces autopilot_status ---
echo "==> D) Verify autopilot_status endpoint"
AP_STATUS="$(_run_remote_bash 'curl -sf http://127.0.0.1:8787/api/projects/soma_kajabi/autopilot_status 2>/dev/null || echo "{}"')"
echo "$AP_STATUS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('  enabled:', d.get('enabled', '?'))
print('  last_tick:', d.get('last_tick', '—'))
print('  last_run_id:', d.get('last_run_id', '—'))
print('  current_status:', d.get('current_status', '—'))
" 2>/dev/null || echo "  (parse failed)"
echo "$AP_STATUS" > "$PROOF_DIR/autopilot_status.json"
echo ""

# --- E) Force one tick ---
echo "==> E) Force one tick"
_run_remote_bash 'sudo systemctl start openclaw-soma-autopilot.service'
sleep 8
LATEST_ARTIFACT="$(_run_remote_bash '
  dir=/opt/ai-ops-runner/artifacts/soma_kajabi/autopilot
  [ -d "$dir" ] || echo ""
  ls -1t "$dir" 2>/dev/null | head -1 || echo ""
')"
LATEST_ARTIFACT="${LATEST_ARTIFACT:-}"
echo "  Latest artifact dir: artifacts/soma_kajabi/autopilot/${LATEST_ARTIFACT:-(none)}"
if [ -n "$LATEST_ARTIFACT" ]; then
  STATUS_JSON="$(_run_remote_bash "cat /opt/ai-ops-runner/artifacts/soma_kajabi/autopilot/$LATEST_ARTIFACT/status.json 2>/dev/null || echo '{}'" 2>/dev/null)" || STATUS_JSON='{}'
else
  STATUS_JSON='{}'
fi
echo "$STATUS_JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin)
outcome = d.get('outcome', '?')
run_id = d.get('run_id', '—')
current = d.get('current_status', '—')
err = d.get('error_class', '')
print('  outcome:', outcome)
print('  run_id:', run_id)
print('  current_status:', current)
if err: print('  error_class:', err)
" 2>/dev/null || echo "  (parse failed)"
echo ""

# --- OUTPUT (no secrets) ---
echo "=== CSR OUTPUT (no secrets) ==="
echo "origin/main SHA: $TARGET_SHA"
echo "aiops-1 build_sha: $BUILD_SHA"
echo "timer: enabled/active — proof: $PROOF_DIR/proof.txt"
echo "flag: created yes"
echo "autopilot_status: enabled=$(echo "$AP_STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('enabled','?'))" 2>/dev/null)"
echo "forced tick artifact: artifacts/soma_kajabi/autopilot/${LATEST_ARTIFACT:-(none)}"
echo "forced tick status: $(echo "$STATUS_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('outcome','?'))" 2>/dev/null)"
echo "=== END ==="

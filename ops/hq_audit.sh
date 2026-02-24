#!/usr/bin/env bash
# hq_audit.sh — DEPRECATED. Use HQ → Actions → Run HQ Audit instead.
#
# This script required OPENCLAW_HQ_TOKEN and tailnet URL reachability from the caller.
# It is not agentic and breaks the "no manual steps" rule.
#
# REPLACEMENT: Use the fully agentic openclaw_hq_audit action:
#   - HQ → Actions → Run HQ Audit (executed by hostd on aiops-1)
#   - Localhost-only (127.0.0.1), no tokens, no tailnet fetch required
#   - Produces artifacts/hq_audit/<run_id>/{SUMMARY.md,SUMMARY.json,LINKS.json}
#   - Self-heal loop (3 retries)
#
# This file is kept for reference only. Do not use in automation.
set -euo pipefail

echo "DEPRECATED: ops/hq_audit.sh is deprecated. Use HQ → Actions → Run HQ Audit instead." >&2
echo "  The agentic action runs on hostd (localhost-only, no tokens, no tailnet fetch)." >&2
exit 1

BASE="${OPENCLAW_HQ_BASE:-https://aiops-1.tailc75c62.ts.net}"
TOKEN="${OPENCLAW_HQ_TOKEN:-}"
REPORT_DIR="${OPENCLAW_AUDIT_REPORT_DIR:-/tmp/hq_audit_$$}"

H_AUTH=""
[ -n "$TOKEN" ] && H_AUTH="-H X-OpenClaw-Token: $TOKEN"

mkdir -p "$REPORT_DIR"

# --- Helpers ---
curl_safe() {
  curl -sS --connect-timeout 10 --max-time 30 -o "$1" -w "%{http_code}" $H_AUTH "$2" 2>/dev/null || echo "000"
}

jq_safe() {
  local f="$1"
  local q="$2"
  [ -f "$f" ] && jq -r "$q" "$f" 2>/dev/null || echo ""
}

# --- 1. API endpoints ---
echo "=== 1. API Endpoints ==="
HP_CODE=$(curl_safe "$REPORT_DIR/health_public.json" "$BASE/api/ui/health_public")
AUTH_CODE=$(curl_safe "$REPORT_DIR/auth_status.json" "$BASE/api/auth/status")
HE_CODE=$(curl_safe "$REPORT_DIR/host_executor.json" "$BASE/api/host-executor/status")
AP_CODE=$(curl_safe "$REPORT_DIR/autopilot.json" "$BASE/api/autopilot/status")

HQ_PASS=true
[ "$HP_CODE" != "200" ] && HQ_PASS=false
[ "$AUTH_CODE" != "200" ] && HQ_PASS=false

echo "  /api/ui/health_public:     $HP_CODE"
echo "  /api/auth/status:          $AUTH_CODE"
echo "  /api/host-executor/status:  $HE_CODE"
echo "  /api/autopilot/status:      $AP_CODE (404 = not present, OK)"
echo ""

# Check for login/2FA requirement
if [ "$AUTH_CODE" = "200" ]; then
  AUTH_BODY=$(cat "$REPORT_DIR/auth_status.json" 2>/dev/null || echo "{}")
  if echo "$AUTH_BODY" | jq -e '.hq_token_required == true' >/dev/null 2>&1; then
    echo "FAIL-CLOSED: Login/2FA required. Stop and instruct user:"
    echo "  1. Open $BASE in browser"
    echo "  2. Complete login/2FA if prompted"
    echo "  3. Set OPENCLAW_HQ_TOKEN to the token from Settings, then re-run this script"
    exit 1
  fi
fi

# --- 2. Runs (last 25, failures) ---
echo "=== 2. Runs (last 25, failures) ==="
RUNS_CODE=$(curl_safe "$REPORT_DIR/runs.json" "$BASE/api/runs?limit=25")
if [ "$RUNS_CODE" = "200" ] && [ -f "$REPORT_DIR/runs.json" ]; then
  FAILURES=$(jq -r '.runs[]? | select(.exit_code != 0 and .exit_code != null) | "\(.run_id)|\(.project_id // "—")|\(.action)|\(.error_summary // .stderr // "—")"' "$REPORT_DIR/runs.json" 2>/dev/null | head -25)
  if [ -n "$FAILURES" ]; then
    echo "$FAILURES" | while IFS='|' read -r rid proj act err; do
      echo "  FAIL: $rid | $proj | $act | ${err:0:80}"
    done
  else
    echo "  No failures in last 25 runs"
  fi
else
  echo "  Could not fetch runs (HTTP $RUNS_CODE)"
fi
echo ""

# --- 3. Actions (Doctor, Port Audit, Restart noVNC) — require TOKEN ---
NOVNC_CODE=""
CONN_CODE=""
echo "=== 3. Actions (Doctor, Port Audit, Restart noVNC) ==="
if [ -z "$TOKEN" ]; then
  echo "  SKIP: OPENCLAW_HQ_TOKEN not set. Set it to run Doctor, Port Audit, Restart noVNC."
else
  # Doctor
  echo "  Triggering Run Doctor..."
  DOCTOR_RESP=$(curl -sS -w "\n%{http_code}" -X POST -H "Content-Type: application/json" -H "X-OpenClaw-Token: $TOKEN" \
    -H "Origin: https://aiops-1.tailc75c62.ts.net" \
    -d '{"action":"doctor"}' "$BASE/api/exec" 2>/dev/null || echo "000")
  DOCTOR_CODE=$(echo "$DOCTOR_RESP" | tail -1)
  DOCTOR_BODY=$(echo "$DOCTOR_RESP" | sed '$d')
  if [ "$DOCTOR_CODE" = "200" ]; then
    echo "    Doctor: triggered (run_id=$(echo "$DOCTOR_BODY" | jq -r '.run_id // "—"'))"
  elif [ "$DOCTOR_CODE" = "409" ]; then
    ACTIVE=$(echo "$DOCTOR_BODY" | jq -r '.active_run_id // empty')
    echo "    Doctor: already running (join run_id=$ACTIVE)"
  else
    echo "    Doctor: HTTP $DOCTOR_CODE"
  fi

  # Port Audit
  echo "  Triggering Show Port Audit..."
  PORTS_RESP=$(curl -sS -w "\n%{http_code}" -X POST -H "Content-Type: application/json" -H "X-OpenClaw-Token: $TOKEN" \
    -H "Origin: https://aiops-1.tailc75c62.ts.net" \
    -d '{"action":"ports"}' "$BASE/api/exec" 2>/dev/null || echo "000")
  PORTS_CODE=$(echo "$PORTS_RESP" | tail -1)
  echo "    Port Audit: HTTP $PORTS_CODE"

  # Restart noVNC (if present — Soma project action)
  echo "  Triggering Restart noVNC..."
  NOVNC_RESP=$(curl -sS -w "\n%{http_code}" -X POST -H "Content-Type: application/json" -H "X-OpenClaw-Token: $TOKEN" \
    -H "Origin: https://aiops-1.tailc75c62.ts.net" \
    -d '{"action":"openclaw_novnc_restart"}' "$BASE/api/projects/soma_kajabi/run" 2>/dev/null || echo "000")
  NOVNC_CODE=$(echo "$NOVNC_RESP" | tail -1)
  echo "    Restart noVNC: HTTP $NOVNC_CODE"
fi
echo ""

# --- 4. Soma Kajabi Phase 0: soma_connectors_status, Session Check ---
echo "=== 4. Soma Kajabi Phase 0 ==="
if [ -z "$TOKEN" ]; then
  echo "  SKIP: OPENCLAW_HQ_TOKEN not set. Set it to run soma_connectors_status and Session Check."
else
  echo "  Triggering soma_connectors_status..."
  CONN_RESP=$(curl -sS -w "\n%{http_code}" -X POST -H "Content-Type: application/json" -H "X-OpenClaw-Token: $TOKEN" \
    -H "Origin: https://aiops-1.tailc75c62.ts.net" \
    -d '{"action":"soma_connectors_status"}' "$BASE/api/projects/soma_kajabi/run" 2>/dev/null || echo "000")
  CONN_CODE=$(echo "$CONN_RESP" | tail -1)
  CONN_BODY=$(echo "$CONN_RESP" | sed '$d')
  echo "    soma_connectors_status: HTTP $CONN_CODE"

  echo "  Triggering Session Check..."
  SESS_RESP=$(curl -sS -w "\n%{http_code}" -X POST -H "Content-Type: application/json" -H "X-OpenClaw-Token: $TOKEN" \
    -H "Origin: https://aiops-1.tailc75c62.ts.net" \
    -d '{"action":"soma_kajabi_session_check"}' "$BASE/api/projects/soma_kajabi/run" 2>/dev/null || echo "000")
  SESS_CODE=$(echo "$SESS_RESP" | tail -1)
  SESS_BODY=$(echo "$SESS_RESP" | sed '$d')
  echo "    Session Check: HTTP $SESS_CODE"

  # WAITING_FOR_HUMAN check
  if echo "$SESS_BODY" | jq -e '.error_class == "WAITING_FOR_HUMAN" or .status == "WAITING_FOR_HUMAN"' >/dev/null 2>&1; then
    echo ""
    echo "*** STOP: WAITING_FOR_HUMAN ***"
    NOVNC_URL=$(echo "$SESS_BODY" | jq -r '.novnc_url // .message // "Check HQ Soma project page for noVNC URL"')
    echo "  noVNC URL: $NOVNC_URL"
    echo "  Instruction: Open noVNC, complete Cloudflare challenge, then rerun Session Check."
    echo ""
    # Continue to report but flag
  fi
fi
echo ""

# --- 5. Final Audit Report ---
echo "=============================================="
echo "         OPENCLAW HQ AUDIT REPORT"
echo "=============================================="
echo ""

# Category PASS/FAIL
HE_OK=$(jq_safe "$REPORT_DIR/host_executor.json" '.ok // false')
HE_HOSTD=$(jq_safe "$REPORT_DIR/host_executor.json" '.hostd_status // "unknown"')
BUILD_SHA=$(jq_safe "$REPORT_DIR/health_public.json" '.build_sha // "unknown"')

echo "Category              | Status"
echo "----------------------|--------"
printf "HQ/API (health,auth)  | %s\n" "$([ "$HQ_PASS" = true ] && echo "PASS" || echo "FAIL")"
printf "Host Executor        | %s\n" "$([ "$HE_CODE" = "200" ] && [ "$HE_OK" = "true" ] && echo "PASS" || echo "FAIL")"
printf "Autopilot            | %s\n" "$([ "$AP_CODE" = "200" ] && echo "PASS" || echo "OK (404=not present)")"
if [ -z "$TOKEN" ]; then
  printf "noVNC (Soma)         | — (run with TOKEN to check)\n"
  printf "Soma Connectors      | — (run with TOKEN to check)\n"
else
  printf "noVNC (Soma)         | %s\n" "$([ "$NOVNC_CODE" = "200" ] && echo "PASS" || echo "FAIL (HTTP ${NOVNC_CODE:-?})")"
  printf "Soma Connectors      | %s\n" "$([ "$CONN_CODE" = "200" ] && echo "PASS" || echo "FAIL (HTTP ${CONN_CODE:-?})")"
fi
echo ""

echo "Build SHA: $BUILD_SHA"
echo ""

# Top 5 failures with links
echo "--- Top 5 Failures (with artifact links) ---"
if [ -f "$REPORT_DIR/runs.json" ]; then
  jq -r '.runs[]? | select(.exit_code != 0 and .exit_code != null) | "\(.run_id)|\(.project_id // "—")|\(.action)|\((.error_summary // .stderr // "—") | tostring | .[0:60])|\(.artifact_dir // "—")"' "$REPORT_DIR/runs.json" 2>/dev/null | head -5 | while IFS='|' read -r rid proj act err art; do
    art=$(echo "$art" | xargs)
    link=""
    [ -n "$art" ] && link="$BASE/$art" || link="(no artifact)"
    echo "  $rid | $proj | $act"
    echo "    Error: $err"
    echo "    Artifact: $link"
    echo ""
  done
else
  echo "  (no runs data)"
fi

echo "--- Recommended Next Fixes ---"
FIXES=()
[ "$HQ_PASS" != "true" ] && FIXES+=("HQ/API unreachable: ensure Tailscale connected, console running on aiops-1")
[ "$HE_CODE" != "200" ] && FIXES+=("Host executor down: check hostd service, OPENCLAW_HOST_EXECUTOR_URL")
[ "$HE_OK" != "true" ] && [ "$HE_CODE" = "200" ] && FIXES+=("Hostd unreachable from console: check network, hostd bind address")
if [ ${#FIXES[@]} -eq 0 ]; then
  echo "  No critical issues detected. Review failures above for project-specific fixes."
else
  for f in "${FIXES[@]}"; do
    echo "  - $f"
  done
fi

echo ""
echo "Report artifacts saved to: $REPORT_DIR"
echo "=============================================="

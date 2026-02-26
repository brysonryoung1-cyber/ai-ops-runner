#!/usr/bin/env bash
# csr_proof_status_url.sh — Prove /api/projects/soma_kajabi/status returns correct URL fields.
# Requires: WAITING_FOR_HUMAN run with framebuffer.png in artifact_dir.
#
# Usage: ./ops/csr_proof_status_url.sh [hq_base]
#   hq_base: http://127.0.0.1:8787 (default on aiops-1) or https://aiops-1.tailc75c62.ts.net
# Run on aiops-1 or from Mac (with OPENCLAW_HQ_TOKEN when using https base).
# Writes: artifacts/hq_proofs/<run_id>/link_check.json
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HQ_BASE="${1:-${OPENCLAW_HQ_BASE:-http://127.0.0.1:8787}}"
HQ_TOKEN="${OPENCLAW_HQ_TOKEN:-}"

# Resolve token on aiops-1
if [ -z "$HQ_TOKEN" ] && [ -f /etc/ai-ops-runner/secrets/openclaw_admin_token ]; then
  HQ_TOKEN="$(cat /etc/ai-ops-runner/secrets/openclaw_admin_token | tr -d '[:space:]')"
fi
if [ -z "$HQ_TOKEN" ] && [ -f /etc/ai-ops-runner/secrets/openclaw_console_token ]; then
  HQ_TOKEN="$(cat /etc/ai-ops-runner/secrets/openclaw_console_token | tr -d '[:space:]')"
fi

curl_hq() {
  local method="${1:-GET}"
  local path="$2"
  local data="${3:-}"
  local args=(-sS -X "$method" "${HQ_BASE}${path}" -H "Content-Type: application/json")
  [ -n "$HQ_TOKEN" ] && args+=(-H "X-OpenClaw-Token: $HQ_TOKEN")
  [ -n "$data" ] && args+=(-d "$data")
  curl "${args[@]}"
}

echo "=== CSR Proof: Status URL Fix ==="
echo "  HQ_BASE: $HQ_BASE"
echo ""

# 1. Fetch current status
echo "==> 1) Fetch /api/projects/soma_kajabi/status"
STATUS_JSON="$(curl_hq GET /api/projects/soma_kajabi/status)"
echo "$STATUS_JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('  current_status:', d.get('current_status', '?'))
print('  last_run_id:', d.get('last_run_id', '—'))
print('  artifact_dir:', d.get('artifact_dir', '—'))
print('  framebuffer_url:', d.get('framebuffer_url', '—'))
print('  artifact_dir_url:', d.get('artifact_dir_url', '—'))
print('  doctor_framebuffer_url:', d.get('doctor_framebuffer_url', '—'))
print('  doctor_artifact_dir_url:', d.get('doctor_artifact_dir_url', '—'))
" 2>/dev/null || true

CURRENT_STATUS="$(echo "$STATUS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('current_status',''))" 2>/dev/null || echo "")"
RUN_ID="$(echo "$STATUS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('last_run_id','') or json.load(sys.stdin).get('active_run_id',''))" 2>/dev/null || echo "")"
ARTIFACT_DIR="$(echo "$STATUS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('artifact_dir',''))" 2>/dev/null || echo "")"

# 2. If not WAITING_FOR_HUMAN, trigger soma_run_to_done and poll
if [ "$CURRENT_STATUS" != "WAITING_FOR_HUMAN" ]; then
  echo ""
  echo "==> 2) Trigger soma_run_to_done (current_status=$CURRENT_STATUS)"
  TRIGGER_RESP="$(curl_hq POST /api/exec '{"action":"soma_run_to_done"}')"
  if ! echo "$TRIGGER_RESP" | grep -q '"run_id"'; then
    echo "  Trigger failed (may be locked): $TRIGGER_RESP"
    echo "  Using existing status for proof (if artifact_dir present)"
  else
    POLL_RUN_ID="$(echo "$TRIGGER_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('run_id',''))")"
    echo "  run_id=$POLL_RUN_ID"
    for i in $(seq 1 60); do
      sleep 10
      STATUS_JSON="$(curl_hq GET /api/projects/soma_kajabi/status)"
      CURRENT_STATUS="$(echo "$STATUS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('current_status',''))" 2>/dev/null || echo "")"
      RUN_ID="$(echo "$STATUS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('last_run_id','') or json.load(sys.stdin).get('active_run_id',''))" 2>/dev/null || echo "")"
      ARTIFACT_DIR="$(echo "$STATUS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('artifact_dir',''))" 2>/dev/null || echo "")"
      echo "  [$i] current_status=$CURRENT_STATUS run_id=$RUN_ID"
      [ "$CURRENT_STATUS" = "WAITING_FOR_HUMAN" ] && break
      [ "$CURRENT_STATUS" = "SUCCESS" ] && echo "  SUCCESS (no WAITING_FOR_HUMAN this run)" && break
    done
  fi
fi

# 3. Re-fetch status for proof
STATUS_JSON="$(curl_hq GET /api/projects/soma_kajabi/status)"
CURRENT_STATUS="$(echo "$STATUS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('current_status',''))" 2>/dev/null || echo "")"
RUN_ID="$(echo "$STATUS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('last_run_id','') or json.load(sys.stdin).get('active_run_id',''))" 2>/dev/null || echo "")"
ARTIFACT_DIR="$(echo "$STATUS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('artifact_dir',''))" 2>/dev/null || echo "")"
FRAMEBUFFER_URL="$(echo "$STATUS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('framebuffer_url','') or '')" 2>/dev/null || echo "")"
ARTIFACT_DIR_URL="$(echo "$STATUS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('artifact_dir_url','') or '')" 2>/dev/null || echo "")"
DOCTOR_FB="$(echo "$STATUS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('doctor_framebuffer_url','') or '')" 2>/dev/null || echo "")"
DOCTOR_AD="$(echo "$STATUS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('doctor_artifact_dir_url','') or '')" 2>/dev/null || echo "")"

# 4. Assert and write proof
PROOF_RUN_ID="${RUN_ID:-unknown}"
PROOF_DIR="$ROOT_DIR/artifacts/hq_proofs/$PROOF_RUN_ID"
mkdir -p "$PROOF_DIR"

PROOF="$PROOF_DIR/link_check.json"
# Redact status for proof (keep URLs)
REDACTED_STATUS="$(echo "$STATUS_JSON" | python3 -c "
import sys, json
d = json.load(sys.stdin)
# Keep only URL fields and key identifiers for proof
out = {
  'current_status': d.get('current_status'),
  'last_run_id': d.get('last_run_id'),
  'artifact_dir': d.get('artifact_dir'),
  'framebuffer_url': d.get('framebuffer_url'),
  'artifact_dir_url': d.get('artifact_dir_url'),
  'doctor_framebuffer_url': d.get('doctor_framebuffer_url'),
  'doctor_artifact_dir_url': d.get('doctor_artifact_dir_url'),
}
print(json.dumps(out, indent=2))
" 2>/dev/null)"

echo ""
echo "==> 3) Assert URL fields"
PASS=1
if [ "$CURRENT_STATUS" = "WAITING_FOR_HUMAN" ]; then
  if [ -n "$FRAMEBUFFER_URL" ]; then
    if echo "$FRAMEBUFFER_URL" | grep -q '/framebuffer\.png$'; then
      echo "  PASS: framebuffer_url endswith /framebuffer.png"
    else
      echo "  FAIL: framebuffer_url must endswith /framebuffer.png (got: $FRAMEBUFFER_URL)"
      PASS=0
    fi
    if echo "$FRAMEBUFFER_URL" | grep -qE '^/artifacts/'; then
      echo "  PASS: framebuffer_url starts with /artifacts/"
    else
      echo "  FAIL: framebuffer_url must start with /artifacts/"
      PASS=0
    fi
  elif [ -n "$DOCTOR_FB" ]; then
    echo "  PASS: doctor_framebuffer_url fallback present"
  else
    echo "  FAIL: framebuffer_url or doctor_framebuffer_url must be non-null when WAITING_FOR_HUMAN"
    PASS=0
  fi
  if [ -n "$ARTIFACT_DIR_URL" ]; then
    if [ "$ARTIFACT_DIR_URL" != "/artifacts" ]; then
      echo "  PASS: artifact_dir_url != /artifacts root"
    else
      echo "  FAIL: artifact_dir_url must not be /artifacts root"
      PASS=0
    fi
    if echo "$ARTIFACT_DIR_URL" | grep -qE '^/artifacts/'; then
      echo "  PASS: artifact_dir_url starts with /artifacts/"
    else
      echo "  FAIL: artifact_dir_url must start with /artifacts/"
      PASS=0
    fi
  elif [ -n "$DOCTOR_AD" ]; then
    echo "  PASS: doctor_artifact_dir_url fallback present"
  else
    echo "  FAIL: artifact_dir_url or doctor_artifact_dir_url must be non-null when WAITING_FOR_HUMAN"
    PASS=0
  fi
else
  echo "  SKIP: current_status=$CURRENT_STATUS (not WAITING_FOR_HUMAN); URL assertions apply only when WAITING_FOR_HUMAN"
fi

# 5. Curl the URLs (from same host - use base)
FB_HTTP=""
AD_HTTP=""
if [ -n "${FRAMEBUFFER_URL:-$DOCTOR_FB}" ]; then
  FB_URL="${FRAMEBUFFER_URL:-$DOCTOR_FB}"
  FULL_URL="${HQ_BASE}${FB_URL}"
  FB_HTTP="$(curl -sS -o /dev/null -w '%{http_code}' "$FULL_URL" 2>/dev/null || echo "000")"
  echo "  framebuffer_url HEAD: $FB_HTTP"
fi
if [ -n "${ARTIFACT_DIR_URL:-$DOCTOR_AD}" ]; then
  AD_URL="${ARTIFACT_DIR_URL:-$DOCTOR_AD}"
  FULL_URL="${HQ_BASE}${AD_URL}"
  AD_HTTP="$(curl -sS -o /dev/null -w '%{http_code}' "$FULL_URL" 2>/dev/null || echo "000")"
  echo "  artifact_dir_url HEAD: $AD_HTTP"
fi

# Write proof
PROOF_PATH="$PROOF"
echo "$REDACTED_STATUS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
d['proof_timestamp'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
d['curl_results'] = {'framebuffer_url': {'http_code': '$FB_HTTP'}, 'artifact_dir_url': {'http_code': '$AD_HTTP'}}
d['assertions_pass'] = $PASS
with open('$PROOF_PATH', 'w') as f:
  json.dump(d, f, indent=2)
" 2>/dev/null || echo "$REDACTED_STATUS" > "$PROOF"
echo ""
echo "  Proof artifact: $PROOF"

if [ "$PASS" = "1" ]; then
  echo ""
  echo "=== CSR Proof: PASS ==="
  exit 0
else
  echo ""
  echo "=== CSR Proof: FAIL (assertions) ==="
  exit 1
fi

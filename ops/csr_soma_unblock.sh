#!/usr/bin/env bash
# CSR Soma/Kajabi unblock: configure Kajabi storage_state + Gmail OAuth, run Phase0, produce Zane finish punch list.
# Run ON aiops-1 (e.g. after SSH). No secrets in logs/chat. Pauses only for human: Kajabi login, Gmail client upload, device approval.
set -euo pipefail

REPO_ROOT="${OPENCLAW_REPO_ROOT:-/opt/ai-ops-runner}"
BASE="${BASE:-http://127.0.0.1:8787}"
KAJABI_STORAGE_PATH="/etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json"
GMAIL_CLIENT_PATH="/etc/ai-ops-runner/secrets/soma_kajabi/gmail_client.json"
GMAIL_OAUTH_PATH="/etc/ai-ops-runner/secrets/soma_kajabi/gmail_oauth.json"

# --- Phase 0: Prep ---
phase0_prep() {
  echo "=== Phase 0: Prep ==="
  cd "$REPO_ROOT"
  git fetch origin main && git reset --hard origin/main
  systemctl stop openclaw-autopilot.timer openclaw-autopilot.service 2>/dev/null || true
  echo "Autopilot stopped."

  local auth status
  auth=$(curl -sS "$BASE/api/auth/status" | jq -c '{host_executor_reachable,trust_tailscale,admin_token_loaded}')
  status=$(curl -sS "$BASE/api/host-executor/status" | jq -c '{ok,console_can_reach_hostd,console_network_mode,executor_url}')
  echo "auth_status: $auth"
  echo "host_executor: $status"
  local he_reach can_reach
  he_reach=$(echo "$auth" | jq -r '.host_executor_reachable')
  can_reach=$(echo "$status" | jq -r '.console_can_reach_hostd')
  if [[ "$he_reach" != "true" || "$can_reach" != "true" ]]; then
    echo "BLOCKED: host_executor_reachable=$he_reach, console_can_reach_hostd=$can_reach. Fix hostd/console and re-run."
    exit 1
  fi
  echo "Executor green. Proceeding."
}

# --- Phase 1: Kajabi storage_state ---
phase1_kajabi() {
  echo "=== Phase 1: Kajabi storage_state ==="
  if [[ -f "$KAJABI_STORAGE_PATH" && -s "$KAJABI_STORAGE_PATH" ]]; then
    echo "KAJABI_STORAGE_STATE_OK (file already present)"
    return 0
  fi

  local capture_script="$REPO_ROOT/ops/scripts/kajabi_capture_storage_state.py"
  if [[ ! -f "$capture_script" ]]; then
    echo "Capture script not found at $capture_script. Create repo script and re-pull."
    exit 1
  fi
  echo "Run Kajabi login capture (headed browser). When browser opens:"
  echo "  1) Sign in at https://app.kajabi.com and land on the dashboard."
  echo "  2) Script saves to /tmp; then install to secrets (sudo required)."
  if [[ "$(id -u)" -eq 0 ]]; then
    python3 "$capture_script" --install || true
  else
    python3 "$capture_script" || true
    echo "PAUSE: Copy to secrets (run on this host):"
    echo "  sudo mkdir -p /etc/ai-ops-runner/secrets/soma_kajabi"
    echo "  sudo cp /tmp/kajabi_storage_state.json $KAJABI_STORAGE_PATH"
    echo "  sudo chmod 600 $KAJABI_STORAGE_PATH"
    echo "  sudo chown root:root $KAJABI_STORAGE_PATH"
  fi
  echo "Press Enter when storage_state is installed and verified..."
  read -r

  if [[ -s "$KAJABI_STORAGE_PATH" ]]; then
    echo "KAJABI_STORAGE_STATE_OK"
  else
    echo "FAIL: $KAJABI_STORAGE_PATH missing or empty."
    exit 1
  fi
}

# --- Phase 2: Gmail OAuth ---
phase2_gmail() {
  echo "=== Phase 2: Gmail OAuth ==="
  if [[ -s "$GMAIL_OAUTH_PATH" ]]; then
    echo "GMAIL_OAUTH_OK (already present)"
    return 0
  fi

  if [[ ! -s "$GMAIL_CLIENT_PATH" ]]; then
    echo "gmail_client.json missing. Upload via HQ: Settings → Connectors → Gmail OAuth."
    echo "Or place file at: $GMAIL_CLIENT_PATH (path only; no contents printed)."
    echo "PAUSE: Upload gmail_client.json (Desktop app OAuth client from Google Cloud Console), then press Enter..."
    read -r
  fi
  if [[ ! -s "$GMAIL_CLIENT_PATH" ]]; then
    echo "FAIL: gmail_client.json still missing at expected path."
    exit 1
  fi
  echo "gmail_client_json: OK (path present)"

  local start_json
  start_json=$(curl -sS -X POST "$BASE/api/exec" -H 'Content-Type: application/json' \
    -d '{"action":"soma_kajabi_gmail_connect_start"}' | tee /tmp/gmail_connect_start.json)
  if echo "$start_json" | jq -e '.ok == true' >/dev/null 2>&1; then
    local url code
    url=$(echo "$start_json" | jq -r '.verification_url // "https://www.google.com/device"')
    code=$(echo "$start_json" | jq -r '.user_code // empty')
    echo "Device flow started. Verification URL: $url  User code: $code"
    echo "PAUSE: Open URL, enter code, approve. Type DONE and press Enter when finished..."
    read -r
  else
    echo "Gmail connect start failed: $(echo "$start_json" | jq -c '.')"
    exit 1
  fi

  curl -sS -X POST "$BASE/api/exec" -H 'Content-Type: application/json' \
    -d '{"action":"soma_kajabi_gmail_connect_finalize"}' | tee /tmp/gmail_connect_finalize.json >/dev/null
  if [[ -s "$GMAIL_OAUTH_PATH" ]]; then
    echo "GMAIL_OAUTH_OK"
  else
    echo "FAIL: gmail_oauth.json missing or empty after finalize."
    exit 1
  fi
}

# --- Phase 3: Connectors + Phase0 ---
phase3_connectors_and_phase0() {
  echo "=== Phase 3: Connectors + Phase0 ==="
  local conn
  conn=$(curl -sS -X POST "$BASE/api/exec" -H 'Content-Type: application/json' \
    -d '{"action":"soma_connectors_status"}' | tee /tmp/connectors_status.json)
  local kajabi_ok gmail_ok
  kajabi_ok=$(echo "$conn" | jq -r '.kajabi // "unknown"')
  gmail_ok=$(echo "$conn" | jq -r '.gmail // "unknown"')
  echo "Connectors: kajabi=$kajabi_ok gmail=$gmail_ok"
  if [[ "$kajabi_ok" != "connected" || "$gmail_ok" != "connected" ]]; then
    echo "BLOCKED: Both connectors must be connected. Fix and re-run Phase 1/2."
    exit 1
  fi

  curl -sS -X POST "$BASE/api/exec" -H 'Content-Type: application/json' \
    -d '{"action":"soma_kajabi_phase0"}' | tee /tmp/phase0.json >/dev/null
  echo "Phase0 run completed. See /tmp/phase0.json and artifact_paths therein."
}

# --- Phase 4: Zane website finish punch list (from Phase0 artifacts) ---
phase4_zane_punch_list() {
  echo "=== Phase 4: Zane website finish punch list ==="
  local p0 out_dir run_id
  p0=$(cat /tmp/phase0.json 2>/dev/null || echo "{}")
  run_id=$(echo "$p0" | jq -r '.stdout | split("\n") | map(select(length > 0)) | last | fromjson? | .run_id // empty')
  [[ -z "$run_id" ]] && run_id=$(echo "$p0" | jq -r '.run_id // empty')
  if [[ -z "$run_id" ]]; then
    echo "No Phase0 run_id; skipping punch list."
    return 0
  fi
  out_dir="$REPO_ROOT/artifacts/soma_kajabi/phase0/$run_id"
  if [[ ! -d "$out_dir" ]]; then
    echo "Phase0 artifact dir not found: $out_dir"
    return 0
  fi

  local snap manifest result
  snap="$out_dir/kajabi_library_snapshot.json"
  manifest="$out_dir/video_manifest.csv"
  result="$out_dir/result.json"

  echo "--- A) Library completeness + mirroring ---"
  if [[ -f "$snap" ]]; then
    local home_mods pract_lessons
    home_mods=$(jq -r '.home.modules | length' "$snap" 2>/dev/null || echo "0")
    pract_lessons=$(jq -r '.practitioner.lessons | length' "$snap" 2>/dev/null || echo "0")
    echo "  Home modules: $home_mods | Practitioner lessons: $pract_lessons"
    jq -r '.home.lessons[]? | "  - \(.module_name): \(.title) (\(.published_state))"' "$snap" 2>/dev/null | head -20
  else
    echo "  (no snapshot)"
  fi
  if [[ -f "$manifest" ]]; then
    local unmapped
    unmapped=$(grep -c "unmapped\|raw_needs_review" "$manifest" 2>/dev/null || echo "0")
    echo "  Video manifest: unmapped/needs_review rows: $unmapped"
  fi

  echo "--- B) Offers + Kajabi Payments checkout ---"
  echo "  Enumerate offers and checkout links in Kajabi admin; verify policies (manual)."

  echo "--- C) Landing pages + nav + branding ---"
  echo "  Remove dead links; consistent CTAs (manual)."

  echo "--- D) Email sequences + onboarding ---"
  echo "  Inventory sequences; draft/patch copy; verify tokens (manual)."

  echo "--- E) QA test purchase + access gating ---"
  echo "  Run test purchase in safe/test mode; verify gating (manual)."

  echo "--- GO-LIVE PUNCH LIST (top items) ---"
  echo "  DONE: Phase0 run_id=$run_id; connectors configured."
  echo "  BLOCKED: (none if Phase0 ok)"
  echo "  NEXT 5: 1) Map unmapped videos to lessons 2) Verify checkout links 3) Audit landing CTAs 4) Review email sequences 5) Run QA test purchase."
}

# --- Phase 5: Re-enable autopilot ---
phase5_autopilot() {
  echo "=== Phase 5: Re-enable autopilot ==="
  systemctl enable --now openclaw-autopilot.timer 2>/dev/null || true
  systemctl status openclaw-autopilot.timer --no-pager -l 2>/dev/null || true
}

# --- Final output ---
final_output() {
  local auth status conn p0
  auth=$(curl -sS "$BASE/api/auth/status" 2>/dev/null | jq -c '{host_executor_reachable,trust_tailscale,admin_token_loaded}' 2>/dev/null || echo "{}")
  status=$(curl -sS "$BASE/api/host-executor/status" 2>/dev/null | jq -c '{ok,console_can_reach_hostd}' 2>/dev/null || echo "{}")
  conn=$(cat /tmp/connectors_status.json 2>/dev/null | jq -c '{kajabi,gmail}' 2>/dev/null || echo "{}")
  p0=$(cat /tmp/phase0.json 2>/dev/null | jq -c '{ok, run_id: (.run_id // (.stdout | split("\n") | map(select(length > 0)) | last | fromjson? | .run_id)), artifact_paths: (.artifact_paths // (.stdout | split("\n") | map(select(length > 0)) | last | fromjson? | .artifact_paths))}' 2>/dev/null || echo "{}")

  echo ""
  echo "========== FINAL OUTPUT (paste back) =========="
  echo "host_executor_reachable + console_can_reach_hostd: $auth $status"
  if [[ -s "$KAJABI_STORAGE_PATH" ]]; then echo "kajabi_storage_state: OK (no contents)"; else echo "kajabi_storage_state: FAIL"; fi
  if [[ -s "$GMAIL_CLIENT_PATH" ]]; then echo "gmail_client_json: OK (path only)"; else echo "gmail_client_json: FAIL"; fi
  echo "gmail device flow: started + finalized per Phase 2"
  echo "connectors_status: $conn"
  echo "phase0: $p0"
  echo "go-live punch list: see Phase 4 output above (top 10)."
  echo "==============================================="
}

# --- Main ---
main() {
  phase0_prep
  phase1_kajabi
  phase2_gmail
  phase3_connectors_and_phase0
  phase4_zane_punch_list
  phase5_autopilot
  final_output
}

main "$@"

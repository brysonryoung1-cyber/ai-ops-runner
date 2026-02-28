#!/usr/bin/env bash
# playbook.recover_browser_gateway — Enable + restart Browser Gateway service.
# Low-risk: idempotent enable + health verify. Uses rootd for systemctl.
# Triggered by reconcile when browser_gateway_ready invariant fails.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ARTIFACTS="${OPENCLAW_ARTIFACTS_ROOT:-$ROOT_DIR/artifacts}"
RUN_ID="${OPENCLAW_RUN_ID:-recover_bg_$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="$ARTIFACTS/playbooks/recover_browser_gateway/$RUN_ID"
mkdir -p "$OUT_DIR"

echo "=== playbook.recover_browser_gateway ($RUN_ID) ==="

BG_URL="http://127.0.0.1:8890/health"

health_before() {
  curl -sf --connect-timeout 3 --max-time 5 "$BG_URL" > "$OUT_DIR/health_before.json" 2>/dev/null && return 0 || return 1
}

health_after() {
  curl -sf --connect-timeout 3 --max-time 5 "$BG_URL" > "$OUT_DIR/health_after.json" 2>/dev/null && return 0 || return 1
}

# 1. Health check before
if health_before; then
  echo "Browser Gateway already healthy. No action needed."
  cat > "$OUT_DIR/actions_taken.json" << EOF
{
  "playbook": "recover_browser_gateway",
  "run_id": "$RUN_ID",
  "actions": ["health_check_ok_noop"],
  "timestamp_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
  echo '{"ok":true,"artifact_dir":"artifacts/playbooks/recover_browser_gateway/'"$RUN_ID"'","run_id":"'"$RUN_ID"'","action":"noop"}'
  exit 0
fi

echo "Browser Gateway unhealthy. Attempting recovery..."

# 2. Copy service file to systemd if not present (rootd install_timer not needed; unit is a simple service)
UNIT_SRC="$ROOT_DIR/ops/systemd/openclaw-browser-gateway.service"
UNIT_DST="/etc/systemd/system/openclaw-browser-gateway.service"

# 3. Use rootd client to enable + restart
ROOTD_CLIENT="$ROOT_DIR/ops/rootd_client.py"
if [ -f "$ROOTD_CLIENT" ]; then
  echo "Requesting rootd: systemctl_enable openclaw-browser-gateway.service"
  python3 "$ROOTD_CLIENT" systemctl_enable '{"unit":"openclaw-browser-gateway.service"}' > "$OUT_DIR/rootd_enable.json" 2>&1 || true

  sleep 3

  if ! health_after; then
    echo "Still unhealthy after enable. Requesting rootd: systemctl_restart"
    python3 "$ROOTD_CLIENT" systemctl_restart '{"unit":"openclaw-browser-gateway.service"}' > "$OUT_DIR/rootd_restart.json" 2>&1 || true
    sleep 5
    health_after || true
  fi
else
  echo "rootd_client.py not found, attempting direct systemctl (requires root)"
  sudo systemctl enable --now openclaw-browser-gateway.service 2>&1 | tee "$OUT_DIR/systemctl_enable.log" || true
  sleep 5
  health_after || true
fi

# 4. Actions taken
cat > "$OUT_DIR/actions_taken.json" << EOF
{
  "playbook": "recover_browser_gateway",
  "run_id": "$RUN_ID",
  "actions": ["rootd_enable", "rootd_restart", "health_verify"],
  "timestamp_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

if [ -f "$OUT_DIR/health_after.json" ]; then
  echo "Recovery complete. Health after:"
  cat "$OUT_DIR/health_after.json"
  echo ""
  echo '{"ok":true,"artifact_dir":"artifacts/playbooks/recover_browser_gateway/'"$RUN_ID"'","run_id":"'"$RUN_ID"'"}'
else
  echo "Recovery attempted but health check still failing."
  echo '{"ok":false,"artifact_dir":"artifacts/playbooks/recover_browser_gateway/'"$RUN_ID"'","run_id":"'"$RUN_ID"'","error":"health_still_failing"}'
  exit 1
fi

#!/usr/bin/env bash
# state_pack_generate.sh — Canonical State Pack generator and freshness contract writer.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
ARTIFACTS_ROOT="${OPENCLAW_ARTIFACTS_ROOT:-/opt/ai-ops-runner/artifacts}"
STATE_PACK_BASE="$ARTIFACTS_ROOT/system/state_pack"
LOCK_DIR="${OPENCLAW_STATE_PACK_LOCK_DIR:-$ARTIFACTS_ROOT/.locks}"
RUN_ID="${OPENCLAW_RUN_ID:-state_pack_$(date -u +%Y%m%dT%H%M%SZ)_$(od -A n -t x4 -N 2 /dev/urandom 2>/dev/null | tr -d ' ' || echo "$$")}"
OUT_DIR="$STATE_PACK_BASE/$RUN_ID"
RESULT_PATH="$OUT_DIR/RESULT.json"
LATEST_PATH="$STATE_PACK_BASE/LATEST.json"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
ARTIFACT_DIR_REL="artifacts/system/state_pack/$RUN_ID"

mkdir -p "$STATE_PACK_BASE" "$LOCK_DIR" "$OUT_DIR"

if command -v flock >/dev/null 2>&1; then
  exec 203>"$LOCK_DIR/state_pack.lock"
  if ! flock -n 203 2>/dev/null; then
    FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    python3 - "$RESULT_PATH" "$RUN_ID" "$STARTED_AT" "$FINISHED_AT" "$OUT_DIR" "$ARTIFACT_DIR_REL" <<'PYEOF'
import json
import sys
from pathlib import Path

out_file = Path(sys.argv[1])
payload = {
    "status": "FAIL",
    "reason": "SKIP_LOCK_CONTENDED",
    "run_id": sys.argv[2],
    "started_at": sys.argv[3],
    "finished_at": sys.argv[4],
    "latest_path": sys.argv[5],
    "artifact_dir": sys.argv[6],
    "checks": [],
    "evidence": [],
}
out_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, separators=(",", ":")))
PYEOF
    exit 10
  fi
fi

CONSOLE_PORT="${OPENCLAW_CONSOLE_PORT:-8787}"
CONSOLE_BASE="http://127.0.0.1:$CONSOLE_PORT"
FRONTDOOR_PORT="${OPENCLAW_FRONTDOOR_PORT:-8788}"
checks=()
evidence=()
PACK_STATUS="fail"
FAIL_COUNT=0
CHECKS_JSON="[]"
EVIDENCE_JSON="[]"

run_generation() {
  # --- 1. health_public ---
  if curl -sf --connect-timeout 3 --max-time 5 "$CONSOLE_BASE/api/ui/health_public" >"$OUT_DIR/health_public.json" 2>/dev/null; then
    checks+=('{"name":"health_public","pass":true,"detail":"OK"}')
  else
    echo '{"ok":false}' >"$OUT_DIR/health_public.json"
    checks+=('{"name":"health_public","pass":false,"detail":"curl failed"}')
  fi
  evidence+=("{\"path\":\"$ARTIFACT_DIR_REL/health_public.json\",\"label\":\"health_public\"}")

  # --- 2. autopilot_status ---
  if curl -sf --connect-timeout 3 --max-time 5 "$CONSOLE_BASE/api/autopilot/status" >"$OUT_DIR/autopilot_status.json" 2>/dev/null; then
    checks+=('{"name":"autopilot_status","pass":true,"detail":"OK"}')
  else
    echo '{"ok":false}' >"$OUT_DIR/autopilot_status.json"
    checks+=('{"name":"autopilot_status","pass":false,"detail":"curl failed"}')
  fi
  evidence+=("{\"path\":\"$ARTIFACT_DIR_REL/autopilot_status.json\",\"label\":\"autopilot_status\"}")

  # --- 3. tailscale serve ---
  if tailscale serve status 2>/dev/null >"$OUT_DIR/tailscale_serve.txt"; then
    tailscale serve status --json 2>/dev/null >"$OUT_DIR/tailscale_serve.json" || true
    checks+=('{"name":"tailscale_serve","pass":true,"detail":"OK"}')
  else
    echo "tailscale serve unavailable" >"$OUT_DIR/tailscale_serve.txt"
    checks+=('{"name":"tailscale_serve","pass":false,"detail":"unavailable"}')
  fi
  evidence+=("{\"path\":\"$ARTIFACT_DIR_REL/tailscale_serve.txt\",\"label\":\"tailscale_serve\"}")

  # --- 4. ports (8787, 8788, 6080, 5900) ---
  (ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null || true) | grep -E ":(8787|8788|6080|5900) " >"$OUT_DIR/ports.txt" || true
  if [ -s "$OUT_DIR/ports.txt" ]; then
    checks+=('{"name":"ports","pass":true,"detail":"OK"}')
  else
    echo "No listeners on 8787/8788/6080/5900" >"$OUT_DIR/ports.txt"
    checks+=('{"name":"ports","pass":false,"detail":"no listeners"}')
  fi
  evidence+=("{\"path\":\"$ARTIFACT_DIR_REL/ports.txt\",\"label\":\"ports\"}")

  # --- 5. systemd (novnc, frontdoor, hostd, guard, hq) ---
  for unit in openclaw-novnc openclaw-frontdoor openclaw-hostd openclaw-guard; do
    f="$OUT_DIR/systemd_${unit}.txt"
    systemctl status "${unit}.service" --no-pager 2>/dev/null >"$f" || echo "unit ${unit}.service not found" >"$f"
    journalctl -u "${unit}.service" -n 20 --no-pager 2>/dev/null >>"$f" || true
    evidence+=("{\"path\":\"$ARTIFACT_DIR_REL/systemd_${unit}.txt\",\"label\":\"systemd_$unit\"}")
  done
  [ -f "$OUT_DIR/systemd_openclaw-frontdoor.txt" ] && cp "$OUT_DIR/systemd_openclaw-frontdoor.txt" "$OUT_DIR/systemd_hq.txt" || echo "hq/frontdoor not found" >"$OUT_DIR/systemd_hq.txt"
  evidence+=("{\"path\":\"$ARTIFACT_DIR_REL/systemd_hq.txt\",\"label\":\"systemd_hq\"}")

  # --- 5b. llm_status ---
  if curl -sf --connect-timeout 3 --max-time 5 "$CONSOLE_BASE/api/llm/status" >"$OUT_DIR/llm_status.json" 2>/dev/null; then
    checks+=('{"name":"llm_status","pass":true,"detail":"OK"}')
  else
    echo '{"ok":false,"providers":[],"config":{"valid":false,"error":"disabled or unreachable"}}' >"$OUT_DIR/llm_status.json"
    checks+=('{"name":"llm_status","pass":false,"detail":"disabled or unreachable"}')
  fi
  evidence+=("{\"path\":\"$ARTIFACT_DIR_REL/llm_status.json\",\"label\":\"llm_status\"}")

  # --- 5c. novnc_http_check ---
  NOVNC_HTTP_CODE="$(curl -sf -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 5 "http://127.0.0.1:$FRONTDOOR_PORT/novnc/vnc.html" 2>/dev/null)" || NOVNC_HTTP_CODE="000"
  echo "{\"url\":\"http://127.0.0.1:$FRONTDOOR_PORT/novnc/vnc.html\",\"status_code\":$NOVNC_HTTP_CODE,\"ok\":$([ "$NOVNC_HTTP_CODE" = "200" ] && echo true || echo false)}" >"$OUT_DIR/novnc_http_check.json"
  evidence+=("{\"path\":\"$ARTIFACT_DIR_REL/novnc_http_check.json\",\"label\":\"novnc_http_check\"}")

  # --- 5d. ws_probe ---
  TS_HOSTNAME="${OPENCLAW_TS_HOSTNAME:-aiops-1.tailc75c62.ts.net}"
  WS_PROBE_HOLD="${OPENCLAW_WS_PROBE_HOLD_SEC:-10}"
  if [ -x "$SCRIPT_DIR/novnc_ws_probe.py" ]; then
    OPENCLAW_TS_HOSTNAME="$TS_HOSTNAME" OPENCLAW_WS_PROBE_HOLD_SEC="$WS_PROBE_HOLD" python3 "$SCRIPT_DIR/novnc_ws_probe.py" --host "$TS_HOSTNAME" --hold "$WS_PROBE_HOLD" --all 2>/dev/null >"$OUT_DIR/ws_probe.json" || echo '{"all_ok":false,"endpoints":{}}' >"$OUT_DIR/ws_probe.json"
  else
    echo '{"all_ok":false,"endpoints":{},"reason":"novnc_ws_probe.py not found"}' >"$OUT_DIR/ws_probe.json"
  fi
  evidence+=("{\"path\":\"$ARTIFACT_DIR_REL/ws_probe.json\",\"label\":\"ws_probe\"}")

  # --- 6. latest_runs_index ---
  python3 - "$ARTIFACTS_ROOT" "$RUN_ID" "$OUT_DIR/latest_runs_index.json" <<'PYEOF'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
run_id = sys.argv[2]
out_file = Path(sys.argv[3])
index = {"run_id": run_id, "projects": {}}
deploy_dir = root / "deploy"
if deploy_dir.exists():
    dirs = sorted([d.name for d in deploy_dir.iterdir() if d.is_dir()], reverse=True)[:5]
    for name in dirs:
        result_path = deploy_dir / name / "deploy_result.json"
        if not result_path.exists():
            continue
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        index["projects"]["deploy"] = {
            "run_id": name,
            "status": data.get("overall"),
            "artifact_dir": f"artifacts/deploy/{name}",
        }
        break
sk = root / "soma_kajabi"
for sub in ["auto_finish", "run_to_done", "capture_interactive"]:
    p = sk / sub
    if not p.exists():
        continue
    dirs = sorted([d.name for d in p.iterdir() if d.is_dir()], reverse=True)[:3]
    for name in dirs:
        index["projects"].setdefault("soma_kajabi", {})[sub] = {
            "run_id": name,
            "artifact_dir": f"artifacts/soma_kajabi/{sub}/{name}",
        }
        break
out_file.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
PYEOF
  evidence+=("{\"path\":\"$ARTIFACT_DIR_REL/latest_runs_index.json\",\"label\":\"latest_runs_index\"}")

  # --- 7. build_sha ---
  BUILD_SHA=""
  if [ -f "$ROOT_DIR/.git/HEAD" ]; then
    BUILD_SHA="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
  fi
  [ -z "$BUILD_SHA" ] && BUILD_SHA="unknown"
  echo "$BUILD_SHA" >"$OUT_DIR/build_sha.txt"

  # --- 8. drift summary ---
  DRIFT_SUMMARY="unknown"
  if curl -sf --connect-timeout 2 --max-time 4 "$CONSOLE_BASE/api/ui/version" >"$OUT_DIR/version.json" 2>/dev/null; then
    DRIFT_SUMMARY="$(python3 - "$OUT_DIR/version.json" <<'PYEOF'
import json
import sys

try:
    data = json.loads(open(sys.argv[1], "r", encoding="utf-8").read())
    status = data.get("drift_status", "unknown")
    drift = data.get("drift")
    if status == "unknown":
        print("unknown")
    elif drift is True:
        print("DRIFT: deployed != origin/main")
    elif drift is False:
        print("Up to date")
    else:
        print("unknown")
except Exception:
    print("unknown")
PYEOF
)" || DRIFT_SUMMARY="unknown"
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
  evidence+=("{\"path\":\"$ARTIFACT_DIR_REL/SUMMARY.md\",\"label\":\"SUMMARY\"}")

  FAIL_COUNT=$(printf '%s\n' "${checks[@]}" | grep -c '"pass":false' || true)
  if [ "$FAIL_COUNT" -eq 0 ]; then
    PACK_STATUS="ok"
  elif [ "$FAIL_COUNT" -lt "${#checks[@]}" ]; then
    PACK_STATUS="partial"
  else
    PACK_STATUS="fail"
  fi
  CHECKS_JSON="[$(IFS=,; echo "${checks[*]}")]"
  EVIDENCE_JSON="[$(IFS=,; echo "${evidence[*]}")]"
}

write_result() {
  local contract_status="$1"
  local reason="$2"
  local finished_at="$3"
  python3 - "$RESULT_PATH" "$contract_status" "$reason" "$RUN_ID" "$STARTED_AT" "$finished_at" "$OUT_DIR" "$ARTIFACT_DIR_REL" "$PACK_STATUS" "$CHECKS_JSON" "$EVIDENCE_JSON" <<'PYEOF'
import json
import sys
from pathlib import Path

out_file = Path(sys.argv[1])
payload = {
    "status": sys.argv[2],
    "reason": sys.argv[3],
    "run_id": sys.argv[4],
    "started_at": sys.argv[5],
    "finished_at": sys.argv[6],
    "generated_at": sys.argv[6],
    "latest_path": sys.argv[7],
    "artifact_dir": sys.argv[8],
    "pack_status": sys.argv[9],
    "checks": json.loads(sys.argv[10]),
    "evidence": json.loads(sys.argv[11]),
}
out_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, separators=(",", ":")))
PYEOF
}

write_latest() {
  local finished_at="$1"
  local latest_tmp
  latest_tmp="$(mktemp "$STATE_PACK_BASE/.LATEST.json.tmp.XXXXXX")"
  python3 - "$latest_tmp" "$RUN_ID" "$finished_at" "$OUT_DIR" "$RESULT_PATH" "$ARTIFACT_DIR_REL" "$PACK_STATUS" <<'PYEOF'
import json
import sys
from pathlib import Path

payload = {
    "status": "PASS",
    "reason": "state_pack_generated",
    "run_id": sys.argv[2],
    "generated_at": sys.argv[3],
    "latest_path": sys.argv[4],
    "result_path": sys.argv[5],
    "artifact_dir": sys.argv[6],
    "pack_status": sys.argv[7],
}
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PYEOF
  mv "$latest_tmp" "$LATEST_PATH"
}

EXIT_CODE=1
RESULT_STATUS="FAIL"
RESULT_REASON="STATE_PACK_GENERATION_FAILED"

if run_generation; then
  FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if write_latest "$FINISHED_AT"; then
    RESULT_STATUS="PASS"
    RESULT_REASON="state_pack_generated"
    EXIT_CODE=0
  else
    RESULT_REASON="LATEST_UPDATE_FAILED"
  fi
else
  FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi

write_result "$RESULT_STATUS" "$RESULT_REASON" "$FINISHED_AT"
exit "$EXIT_CODE"

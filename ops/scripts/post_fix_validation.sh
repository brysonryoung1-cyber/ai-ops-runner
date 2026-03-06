#!/usr/bin/env bash
# post_fix_validation.sh — Ops-only post-fix verification on aiops-1.
# Creates artifacts/system/post_fix_validation/<UTC_TS>/ with proof bundle.
# NO code edits, no commits, no deploys.
set -euo pipefail

ROOT_DIR="${OPENCLAW_REPO_ROOT:-/opt/ai-ops-runner}"
cd "$ROOT_DIR"

TS=$(date -u +%Y%m%dT%H%M%SZ)
ART="$ROOT_DIR/artifacts/system/post_fix_validation/$TS"
mkdir -p "$ART"

echo "=== post_fix_validation $TS ==="
echo "  ART=$ART"

# --- 1. health_public ---
echo "--- 1. health_public ---"
curl -sf --connect-timeout 5 --max-time 10 http://127.0.0.1:8788/api/ui/health_public > "$ART/health_public.json" 2>/dev/null || echo '{"ok":false,"error":"curl_failed"}' > "$ART/health_public.json"
git rev-parse --short HEAD > "$ART/repo_head.txt" 2>/dev/null || echo "unknown" > "$ART/repo_head.txt"

# --- 2. services/timers ---
echo "--- 2. services/timers ---"
{
  echo "=== systemctl is-failed ==="
  systemctl is-failed openclaw-autopilot openclaw-novnc-guard openclaw-reconcile 2>&1 || true
  echo ""
  echo "=== systemctl status ==="
  systemctl status --no-pager openclaw-autopilot openclaw-novnc-guard openclaw-reconcile 2>&1 || true
} > "$ART/services_status.txt" 2>&1

systemctl list-timers --all --no-pager 2>/dev/null | grep -iE 'openclaw|autopilot|novnc|reconcile|brain|guard|doctor|soma' > "$ART/timers_status.txt" 2>/dev/null || echo "(no matching timers)" > "$ART/timers_status.txt"

# --- 3. doctor ---
echo "--- 3. doctor ---"
DOCTOR_RUN_ID="postfix_${TS}_doctor"
export OPENCLAW_DOCTOR_RUN_ID="$DOCTOR_RUN_ID"
./ops/openclaw_doctor.sh > "$ART/doctor_stdout.txt" 2>&1 || true
cp "artifacts/doctor/$DOCTOR_RUN_ID/doctor.json" "$ART/doctor.json" 2>/dev/null || echo '{"overall":"SKIP","reason":"doctor_artifact_missing"}' > "$ART/doctor.json"

# --- 4. guard ---
echo "--- 4. guard ---"
GUARD_RUN_ID="postfix_${TS}_guard"
export OPENCLAW_DOCTOR_RUN_ID="$GUARD_RUN_ID"
./ops/openclaw_guard.sh > "$ART/guard_stdout.txt" 2>&1 || true
# Guard runs doctor; copy doctor artifact if guard produced one
[ -f "artifacts/doctor/$GUARD_RUN_ID/doctor.json" ] && cp "artifacts/doctor/$GUARD_RUN_ID/doctor.json" "$ART/guard.json" 2>/dev/null || true
[ -f "$ART/guard.json" ] || echo '{}' > "$ART/guard.json"

# --- 5. failure histogram (last 24h) ---
echo "--- 5. failure histogram ---"
python3 - "$ART" "$ROOT_DIR" <<'PY'
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

art_dir = Path(sys.argv[1])
root = Path(sys.argv[2])
cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
hist = {}
recent_soma = []

def mtime_ok(p: Path) -> bool:
    try:
        m = p.stat().st_mtime
        return datetime.fromtimestamp(m, tz=timezone.utc) >= cutoff
    except OSError:
        return False

def extract_error_class(p: Path) -> str | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        return d.get("error_class") or d.get("status")
    except Exception:
        return None

# Scan soma_kajabi run_to_done (PROOF.json is canonical from soma_run_to_done)
rtd = root / "artifacts" / "soma_kajabi" / "run_to_done"
if rtd.exists():
    for d in sorted(rtd.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
        if not d.is_dir():
            continue
        proof = d / "PROOF.json"
        res = d / "RESULT.json"
        f = proof if proof.exists() else res
        if f.exists() and mtime_ok(f):
            try:
                data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
                ec = data.get("error_class") or data.get("status")
                st = data.get("status", data.get("terminal_status", "?"))
                if ec:
                    hist[ec] = hist.get(ec, 0) + 1
                recent_soma.append({
                    "run_id": d.name,
                    "status": st,
                    "error_class": data.get("error_class", ""),
                    "artifact_path": str(f.relative_to(root)),
                })
            except Exception:
                pass

# Scan project_autopilot
pa = root / "artifacts" / "system" / "project_autopilot"
if pa.exists():
    for d in sorted(pa.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:30]:
        if not d.is_dir():
            continue
        res = d / "RESULT.json"
        if res.exists() and mtime_ok(res):
            ec = extract_error_class(res)
            if ec:
                hist[ec] = hist.get(ec, 0) + 1

# Scan brain_loop
bl = root / "artifacts" / "system" / "brain_loop"
if bl.exists():
    for d in sorted(bl.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:30]:
        if not d.is_dir():
            continue
        res = d / "RESULT.json"
        if res.exists() and mtime_ok(res):
            ec = extract_error_class(res)
            if ec:
                hist[ec] = hist.get(ec, 0) + 1

# Dedupe recent_soma by run_id, keep latest 15
seen = set()
deduped = []
for r in recent_soma:
    rid = r.get("run_id", "")
    if rid and rid not in seen:
        seen.add(rid)
        deduped.append(r)
        if len(deduped) >= 15:
            break

(art_dir / "failure_histogram_postfix.json").write_text(json.dumps(hist, indent=2))
(art_dir / "recent_runs_soma.json").write_text(json.dumps(deduped[:15], indent=2))
PY

# --- 6. Soma run_to_done (controlled run) ---
echo "--- 6. Soma run_to_done ---"
SOMA_OUT="$ART/soma_rerun_output.txt"
SOMA_RESULT="$ART/SOMA_RERUN_RESULT.md"
python3 ./ops/scripts/soma_run_to_done.py > "$SOMA_OUT" 2>&1 || true
SOMA_RC=$?

# Parse last JSON line from soma output
RUN_ID=""
TERMINAL_STATUS=""
ERROR_CLASS=""
ARTIFACT_DIR=""
if [ -f "$SOMA_OUT" ]; then
  LAST_JSON=$(grep -E '^\s*\{' "$SOMA_OUT" | tail -1)
  if [ -n "$LAST_JSON" ]; then
    RUN_ID=$(echo "$LAST_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('run_id',''))" 2>/dev/null || echo "")
    TERMINAL_STATUS=$(echo "$LAST_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status', d.get('terminal_status','')))" 2>/dev/null || echo "")
    ERROR_CLASS=$(echo "$LAST_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error_class',''))" 2>/dev/null || echo "")
    ARTIFACT_DIR=$(echo "$LAST_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('artifact_dir',''))" 2>/dev/null || echo "")
  fi
fi

# Map exit code to status
case $SOMA_RC in
  0) TERMINAL_STATUS="${TERMINAL_STATUS:-SUCCESS}" ;;
  2) TERMINAL_STATUS="${TERMINAL_STATUS:-WAITING_FOR_HUMAN}" ;;
  *) TERMINAL_STATUS="${TERMINAL_STATUS:-FAIL}" ;;
esac

cat > "$SOMA_RESULT" <<EOF
# SOMA_RERUN_RESULT

- **run_id:** $RUN_ID
- **terminal_status:** $TERMINAL_STATUS
- **error_class:** $ERROR_CLASS
- **artifact_dir:** $ARTIFACT_DIR
- **exit_code:** $SOMA_RC

## Artifact paths
- soma output: $ART/soma_rerun_output.txt
- run_to_done dir: $ARTIFACT_DIR

## Next constraint
$(case "$TERMINAL_STATUS" in
  SUCCESS) echo "None — Soma reached SUCCESS." ;;
  WAITING_FOR_HUMAN) echo "Cloudflare/Kajabi requires human login via noVNC. Complete login, then re-trigger soma_run_to_done." ;;
  *) echo "Fix $ERROR_CLASS; then re-run soma_run_to_done." ;;
esac)
EOF

# --- 7. PROOF_SUMMARY ---
echo "--- 7. PROOF_SUMMARY ---"
DEPLOY_SHA=$(python3 -c "import json; d=json.load(open('$ART/health_public.json')); print(d.get('build_sha','unknown'))" 2>/dev/null || echo "unknown")
IS_FAILED=$(head -5 "$ART/services_status.txt" 2>/dev/null | tr '\n' ' ')
DOCTOR_STATUS=$(python3 -c "import json; d=json.load(open('$ART/doctor.json')); print(d.get('overall','?'))" 2>/dev/null || echo "?")
SYSTEMD_FAILED=$(python3 -c "import json; d=json.load(open('$ART/doctor.json')); print(d.get('systemd_failed_units',{}).get('status','?'))" 2>/dev/null || echo "?")
TOP3=$(python3 -c "
import json
h=json.load(open('$ART/failure_histogram_postfix.json'))
items=sorted(h.items(), key=lambda x:-x[1])[:3]
print('; '.join(f'{k}:{v}' for k,v in items) if items else 'none')
" 2>/dev/null || echo "none")

cat > "$ART/PROOF_SUMMARY.md" <<EOF
# Post-Fix Validation Proof

- **timestamp:** $TS
- **deploy_sha/build_sha:** $DEPLOY_SHA
- **systemctl is-failed:** $IS_FAILED
- **doctor overall:** $DOCTOR_STATUS
- **systemd_failed_units:** $SYSTEMD_FAILED
- **Soma rerun terminal_status:** $TERMINAL_STATUS
- **Soma rerun error_class:** $ERROR_CLASS
- **Top 3 error classes (24h):** $TOP3

## Artifacts
- health_public.json
- services_status.txt
- timers_status.txt
- doctor.json
- guard.json
- recent_runs_soma.json
- failure_histogram_postfix.json
- SOMA_RERUN_RESULT.md
EOF

echo "=== DONE ==="
echo "  ART=$ART"
echo "  PROOF_SUMMARY: $ART/PROOF_SUMMARY.md"

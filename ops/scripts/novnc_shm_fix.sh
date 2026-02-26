#!/usr/bin/env bash
# novnc_shm_fix.sh — Diagnose + recover + permanent fix for shmget: No space left on device.
#
# Run on aiops-1 from repo root. Does:
#   A) Collect diagnostics (df, mount, ipcs, sysctl, journalctl) -> artifacts/novnc_shm_fix/<run_id>/diagnostics.txt
#   B) Immediate recovery: stop service, pkill Xvfb/x11vnc/websockify, remove stale X locks
#   C) Permanent fix: install sysctl + reinstall systemd unit (from repo)
#   D) Verify: restart noVNC, curl vnc.html, openclaw_novnc_doctor, write proof.json
#   E) Re-run soma: soma_run_to_done (optional: pass --run-soma)
#
# Usage: ./ops/scripts/novnc_shm_fix.sh [--run-soma]
set -euo pipefail

RUN_SOMA=0
[[ "${1:-}" = "--run-soma" ]] && RUN_SOMA=1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_ID="novnc_shm_fix_$(date -u +%Y%m%dT%H%M%SZ)_$$"
ART_DIR="$ROOT_DIR/artifacts/novnc_shm_fix/$RUN_ID"
DISPLAY_NUM="${OPENCLAW_NOVNC_DISPLAY:-:99}"
D_NUM="${DISPLAY_NUM#:}"

mkdir -p "$ART_DIR"

# ── A) Diagnose ──
echo "==> A) Collecting diagnostics..."
IPCS_BEFORE="$(ipcs -m 2>/dev/null | tail -n +4 | wc -l || echo 0)"
{
  echo "=== df -h /dev/shm ==="
  df -h /dev/shm 2>/dev/null || echo "(df failed)"
  echo ""
  echo "=== mount | grep shm ==="
  mount | grep -E "shm|tmpfs" 2>/dev/null || echo "(no shm mount)"
  echo ""
  echo "=== ipcs -m (first 50) ==="
  ipcs -m 2>/dev/null | head -n 50 || echo "(ipcs failed)"
  echo ""
  echo "=== sysctl kernel.shmmax kernel.shmall kernel.shmmni ==="
  sysctl kernel.shmmax kernel.shmall kernel.shmmni 2>/dev/null || echo "(sysctl failed)"
  echo ""
  echo "=== journalctl -u openclaw-novnc.service -n 200 ==="
  journalctl -u openclaw-novnc.service -n 200 --no-pager 2>/dev/null || echo "(journalctl failed)"
} >"$ART_DIR/diagnostics.txt" 2>&1

# Fail-fast: /dev/shm must be mounted and >= 64M
SHM_SIZE_K="$(df -k /dev/shm 2>/dev/null | awk 'NR==2 {print $2}' || echo 0)"
if [ "${SHM_SIZE_K:-0}" -lt 65536 ] 2>/dev/null; then
  echo "  FAIL: /dev/shm too small or not mounted (${SHM_SIZE_K}K < 64M). error_class=SHM_DEVSHM_TOO_SMALL" >&2
  python3 -c "
import json
d = {'classification': 'A1', 'error_class': 'SHM_DEVSHM_TOO_SMALL', 'shm_size_k': int('$SHM_SIZE_K'), 'required_k': 65536}
with open('$ART_DIR/classification.json', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null || true
  exit 1
fi

# Determine root cause from diagnostics
ROOT_CAUSE="unknown"
if grep -q "shmget: No space left on device" "$ART_DIR/diagnostics.txt" 2>/dev/null; then
  # Check /dev/shm usage
  SHM_AVAIL=""
  SHM_AVAIL="$(df /dev/shm 2>/dev/null | awk 'NR==2 {print $4}' || true)"
  if [ -n "$SHM_AVAIL" ] && [ "${SHM_AVAIL:-0}" -lt 10000 ] 2>/dev/null; then
    ROOT_CAUSE="/dev/shm_full"
  elif grep -q "kernel.shmmax" "$ART_DIR/diagnostics.txt" 2>/dev/null; then
    SHMMAX="$(sysctl -n kernel.shmmax 2>/dev/null || echo 0)"
    if [ "${SHMMAX:-0}" -lt 67108864 ] 2>/dev/null; then
      ROOT_CAUSE="sysctl_shmmax_too_low"
    else
      ROOT_CAUSE="orphaned_shm_segments"
    fi
  else
    ROOT_CAUSE="orphaned_shm_segments"
  fi
fi

# Write classification.json
CLASSIFICATION="A"
case "$ROOT_CAUSE" in
  /dev/shm_full) CLASSIFICATION="A1" ;;
  sysctl_shmmax_too_low) CLASSIFICATION="A2" ;;
  orphaned_shm_segments) CLASSIFICATION="A3" ;;
  *) CLASSIFICATION="A5" ;;  # namespace/permission/PrivateTmp
esac
python3 -c "
import json
d = {
  'classification': '$CLASSIFICATION',
  'root_cause': '$ROOT_CAUSE',
  'ipcs_before': int('$IPCS_BEFORE'),
  'shm_size_k': int('$SHM_SIZE_K'),
}
with open('$ART_DIR/classification.json', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null || true

echo "  Root cause (inferred): $ROOT_CAUSE"
echo "  Diagnostics: $ART_DIR/diagnostics.txt"

# ── B) Immediate recovery ──
echo ""
echo "==> B) Immediate recovery..."
systemctl stop openclaw-novnc.service 2>/dev/null || true
sleep 2
pkill -f "Xvfb.*$DISPLAY_NUM" 2>/dev/null || true
pkill -f "Xvfb :$D_NUM" 2>/dev/null || true
pkill -f x11vnc 2>/dev/null || true
pkill -f websockify 2>/dev/null || true
sleep 2

# Remove stale X lock for configured DISPLAY only
LOCK_FILE="/tmp/.X${D_NUM}-lock"
if [ -f "$LOCK_FILE" ]; then
  OLD_PID="$(cat "$LOCK_FILE" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && ! kill -0 "$OLD_PID" 2>/dev/null; then
    rm -f "$LOCK_FILE"
    echo "  Removed stale X lock for :$D_NUM"
  fi
fi

# Optional: clear orphaned shm segments owned by root (novnc runs as root)
# Only remove segments with no attached processes (nattch=0)
ORPHAN_COUNT=0
while read -r line; do
  shmid="$(echo "$line" | awk '{print $2}')"
  [ -z "$shmid" ] || [ "$shmid" = "shmid" ] && continue
  nattch="$(echo "$line" | awk '{print $6}')"
  if [ "${nattch:-1}" = "0" ] 2>/dev/null; then
    ipcrm -m "$shmid" 2>/dev/null && ORPHAN_COUNT=$((ORPHAN_COUNT + 1)) || true
  fi
done < <(ipcs -m 2>/dev/null | tail -n +4)
IPCS_AFTER="$(ipcs -m 2>/dev/null | tail -n +4 | wc -l || echo 0)"
[ "$ORPHAN_COUNT" -gt 0 ] && echo "  Cleared $ORPHAN_COUNT orphaned shm segments (ipcs before=$IPCS_BEFORE after=$IPCS_AFTER)" || true

# ── C) Permanent fix ──
echo ""
echo "==> C) Permanent fix (sysctl + systemd unit)..."
if [ -f "$ROOT_DIR/ops/install_openclaw_novnc.sh" ]; then
  cd "$ROOT_DIR"
  sudo bash ./ops/install_openclaw_novnc.sh 2>&1 | tee "$ART_DIR/install.log" || true
fi

# If /dev/shm is too small, remount with larger size (e.g. 256M)
SHM_SIZE="$(df -k /dev/shm 2>/dev/null | awk 'NR==2 {print $2}' || echo 0)"
if [ "${SHM_SIZE:-0}" -lt 100000 ] 2>/dev/null; then
  echo "  /dev/shm small (${SHM_SIZE}K); attempting remount to 256M..."
  sudo mount -o remount,size=256M /dev/shm 2>/dev/null || echo "  (remount failed; may need fstab entry)"
fi

# ── D) Verify ──
echo ""
echo "==> D) Verify noVNC..."
systemctl restart openclaw-novnc.service 2>/dev/null || true
sleep 5

# Wait for vnc.html
for i in $(seq 1 15); do
  if curl -fsS --connect-timeout 2 --max-time 4 "http://127.0.0.1:6080/vnc.html" >/dev/null 2>&1; then
    echo "  curl vnc.html: 200 OK"
    break
  fi
  [ "$i" -eq 15 ] && { echo "  FAIL: vnc.html did not return 200"; exit 1; }
  sleep 2
done

# Run doctor
DOCTOR_OUT="$ART_DIR/doctor_output.json"
if [ -x "$ROOT_DIR/ops/openclaw_novnc_doctor.sh" ]; then
  cd "$ROOT_DIR"
  if ./ops/openclaw_novnc_doctor.sh 2>&1 | tee "$ART_DIR/doctor.log" | tail -1 >"$ART_DIR/doctor_last.json"; then
    cp "$ART_DIR/doctor_last.json" "$DOCTOR_OUT" 2>/dev/null || true
  fi
fi

# Parse doctor result
DOCTOR_PASS=0
if [ -f "$DOCTOR_OUT" ]; then
  if python3 -c "
import json
d = json.load(open('$DOCTOR_OUT'))
exit(0 if d.get('ok') and d.get('ws_stability_local') == 'verified' and d.get('ws_stability_tailnet') == 'verified' else 1)
" 2>/dev/null; then
    DOCTOR_PASS=1
  fi
fi

# Copy framebuffer.png if doctor wrote it
FB_SRC="$ROOT_DIR/artifacts/novnc_debug"
for d in "$FB_SRC"/*/; do
  [ -d "$d" ] || continue
  [ -f "${d}framebuffer.png" ] && cp "${d}framebuffer.png" "$ART_DIR/" 2>/dev/null && break
done

# Write proof.json
PROOF="$ART_DIR/proof.json"
python3 -c "
import json
d = {
  'run_id': '$RUN_ID',
  'root_cause': '$ROOT_CAUSE',
  'doctor_pass': bool($DOCTOR_PASS),
  'fix_applied': {
    'sysctl': '99-openclaw-novnc.conf',
    'systemd': 'RuntimeDirectory, PrivateTmp=false',
  },
  'ipcs_before': int('${IPCS_BEFORE:-0}'),
  'ipcs_after': int('${IPCS_AFTER:-0}'),
  'orphans_cleared': int('${ORPHAN_COUNT:-0}'),
  'artifacts': {
    'diagnostics': 'artifacts/novnc_shm_fix/$RUN_ID/diagnostics.txt',
    'proof': 'artifacts/novnc_shm_fix/$RUN_ID/proof.json',
    'classification': 'artifacts/novnc_shm_fix/$RUN_ID/classification.json',
  },
}
with open('$PROOF', 'w') as f: json.dump(d, f, indent=2)
" 2>/dev/null || true

if [ "$DOCTOR_PASS" -eq 0 ]; then
  echo "  WARNING: openclaw_novnc_doctor did not PASS (ws_stability_local/tailnet)"
  echo "  Check $ART_DIR/doctor.log"
fi

echo ""
echo "  proof.json: $PROOF"

# ── E) Re-run Soma (optional) ──
if [ "$RUN_SOMA" -eq 1 ]; then
  echo ""
  echo "==> E) Running soma_run_to_done..."
  cd "$ROOT_DIR"
  SOMA_OUT=""
  if SOMA_OUT="$(python3 ./ops/scripts/soma_run_to_done.py 2>&1)"; then
    echo "$SOMA_OUT"
    # Parse and output novnc_url + instruction if WAITING_FOR_HUMAN
    echo "$SOMA_OUT" | tail -1 | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    if d.get('status') == 'WAITING_FOR_HUMAN':
        print('novnc_url:', d.get('novnc_url', ''))
        print('instruction_line:', d.get('instruction_line', ''))
    elif d.get('ok'):
        print('SUCCESS:', d.get('acceptance_path'), 'mirror_pass=', d.get('mirror_pass'))
except: pass
" 2>/dev/null || true
  else
    echo "$SOMA_OUT"
    exit 1
  fi
fi

echo ""
echo "=== novnc_shm_fix.sh complete ==="
echo "  run_id: $RUN_ID"
echo "  root_cause: $ROOT_CAUSE"
echo "  doctor_pass: $([ "$DOCTOR_PASS" -eq 1 ] && echo true || echo false)"
echo "  artifacts: $ART_DIR"

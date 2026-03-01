#!/usr/bin/env bash
# shm_gc_orphans.sh — Remove orphaned SysV shared memory segments (nattch=0, age>60s).
#
# Safe/idempotent: exits 0 if ipcs unavailable or no segments exist.
# Intended as ExecStartPre for openclaw-novnc.service and callable from guard timer.
#
# Modes:
#   (default)    remove orphaned segments
#   --dry-run    print what would be removed, change nothing
set -euo pipefail

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

if ! command -v ipcs >/dev/null 2>&1; then
  echo "shm_gc_orphans: ipcs not found; nothing to do."
  exit 0
fi

NOW_EPOCH="$(date +%s)"
AGE_THRESHOLD=60
REMOVED=0
SKIPPED=0
TOTAL=0

HAS_EXTENDED=0
if ipcs -m -o >/dev/null 2>&1; then
  HAS_EXTENDED=1
fi

BEFORE_COUNT="$(ipcs -m 2>/dev/null | tail -n +4 | grep -cE '^0x' || true)"
BEFORE_COUNT="${BEFORE_COUNT:-0}"

if [ "$HAS_EXTENDED" -eq 1 ]; then
  while IFS= read -r line; do
    [[ "$line" =~ ^0x ]] || continue
    TOTAL=$((TOTAL + 1))

    shmid="$(echo "$line" | awk '{print $2}')"
    nattch="$(echo "$line" | awk '{print $6}')"
    # ipcs -m -o adds cpid/lpid columns; status field may vary — use last-change time
    # Standard `ipcs -m` columns: key shmid owner perms bytes nattch status
    # We need nattch=0 to consider removal

    [ -z "$shmid" ] && continue
    [ "${nattch:-1}" != "0" ] && { SKIPPED=$((SKIPPED + 1)); continue; }

    # Try to get age from /proc/sysvipc/shm (Linux-specific, more reliable)
    SEG_AGE=""
    if [ -f /proc/sysvipc/shm ]; then
      # /proc/sysvipc/shm columns include ctime (change time) as epoch
      SEG_CTIME="$(awk -v id="$shmid" '$2 == id {print $14}' /proc/sysvipc/shm 2>/dev/null || true)"
      if [ -n "$SEG_CTIME" ] && [ "$SEG_CTIME" -gt 0 ] 2>/dev/null; then
        SEG_AGE=$(( NOW_EPOCH - SEG_CTIME ))
      fi
    fi

    if [ -n "$SEG_AGE" ]; then
      if [ "$SEG_AGE" -lt "$AGE_THRESHOLD" ]; then
        SKIPPED=$((SKIPPED + 1))
        continue
      fi
    fi
    # If age cannot be determined (no /proc/sysvipc/shm), still remove nattch=0
    # segments — they are definitively orphaned since nothing is attached.

    if [ "$DRY_RUN" -eq 1 ]; then
      echo "  [dry-run] would remove shmid=$shmid nattch=$nattch age=${SEG_AGE:-unknown}s"
      REMOVED=$((REMOVED + 1))
    else
      if ipcrm -m "$shmid" 2>/dev/null; then
        REMOVED=$((REMOVED + 1))
      fi
    fi
  done < <(ipcs -m 2>/dev/null | tail -n +4)
else
  # Fallback: no -o flag; parse standard ipcs -m output, skip age check
  while IFS= read -r line; do
    [[ "$line" =~ ^0x ]] || continue
    TOTAL=$((TOTAL + 1))

    shmid="$(echo "$line" | awk '{print $2}')"
    nattch="$(echo "$line" | awk '{print $6}')"

    [ -z "$shmid" ] && continue
    [ "${nattch:-1}" != "0" ] && { SKIPPED=$((SKIPPED + 1)); continue; }

    # Try /proc/sysvipc/shm age check even without -o
    SEG_AGE=""
    if [ -f /proc/sysvipc/shm ]; then
      SEG_CTIME="$(awk -v id="$shmid" '$2 == id {print $14}' /proc/sysvipc/shm 2>/dev/null || true)"
      if [ -n "$SEG_CTIME" ] && [ "$SEG_CTIME" -gt 0 ] 2>/dev/null; then
        SEG_AGE=$(( NOW_EPOCH - SEG_CTIME ))
      fi
    fi

    if [ -n "$SEG_AGE" ] && [ "$SEG_AGE" -lt "$AGE_THRESHOLD" ]; then
      SKIPPED=$((SKIPPED + 1))
      continue
    fi

    if [ "$DRY_RUN" -eq 1 ]; then
      echo "  [dry-run] would remove shmid=$shmid nattch=$nattch age=${SEG_AGE:-unknown}s"
      REMOVED=$((REMOVED + 1))
    else
      if ipcrm -m "$shmid" 2>/dev/null; then
        REMOVED=$((REMOVED + 1))
      fi
    fi
  done < <(ipcs -m 2>/dev/null | tail -n +4)
fi

AFTER_COUNT="$(ipcs -m 2>/dev/null | tail -n +4 | grep -cE '^0x' || true)"
AFTER_COUNT="${AFTER_COUNT:-0}"

MODE="gc"
[ "$DRY_RUN" -eq 1 ] && MODE="dry-run"
echo "shm_gc_orphans [$MODE]: before=$BEFORE_COUNT removed=$REMOVED skipped=$SKIPPED after=$AFTER_COUNT"
exit 0

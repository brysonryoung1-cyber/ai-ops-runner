#!/usr/bin/env bash
# state_pack_prune.sh — enforce state-pack retention and disk guard.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

ARTIFACTS_ROOT="/opt/ai-ops-runner/artifacts"
KEEP_COUNT="${STATE_PACK_KEEP_COUNT:-288}"
KEEP_HOURS="${STATE_PACK_KEEP_HOURS:-}"
DISK_THRESHOLD_PCT="${STATE_PACK_DISK_THRESHOLD_PCT:-85}"
KEEP_COUNT_MIN="${STATE_PACK_KEEP_COUNT_MIN:-24}"
DISK_GUARD_KEEP_HOURS="${STATE_PACK_DISK_GUARD_KEEP_HOURS:-24}"

usage() {
  cat <<'EOF'
Usage: state_pack_prune.sh [--root PATH] [--keep-count N] [--keep-hours H] [--disk-threshold-pct P]
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --root)
      ARTIFACTS_ROOT="${2:-}"; shift 2 ;;
    --keep-count)
      KEEP_COUNT="${2:-}"; shift 2 ;;
    --keep-hours)
      KEEP_HOURS="${2:-}"; shift 2 ;;
    --disk-threshold-pct)
      DISK_THRESHOLD_PCT="${2:-}"; shift 2 ;;
    -h|--help)
      usage
      exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1 ;;
  esac
done

STATE_PACK_DIR="$ARTIFACTS_ROOT/system/state_pack"
PRUNE_REPORT="$STATE_PACK_DIR/PRUNE_LAST.json"
mkdir -p "$STATE_PACK_DIR"

USED_PCT="${STATE_PACK_FAKE_USED_PCT:-}"
if [ -z "$USED_PCT" ]; then
  USED_PCT="$(df -P "$ARTIFACTS_ROOT" 2>/dev/null | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
fi
[ -z "$USED_PCT" ] && USED_PCT="0"

LATEST_PATH="$(
  python3 - "$ROOT_DIR" "$STATE_PACK_DIR/LATEST.json" <<'PYEOF'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from ops.lib.state_pack_contract import load_latest

latest = load_latest(Path(sys.argv[2]))
print(latest.get("latest_path") or "")
PYEOF
)" || LATEST_PATH=""

MODE="normal"
ALERT_REASON="RETENTION_APPLIED"
EFFECTIVE_KEEP_COUNT="$KEEP_COUNT"
EFFECTIVE_KEEP_HOURS="$KEEP_HOURS"
if [ "${USED_PCT:-0}" -ge "${DISK_THRESHOLD_PCT:-0}" ]; then
  MODE="disk_guard"
  ALERT_REASON="DISK_GUARD_ACTIVE"
  if [ "$KEEP_COUNT_MIN" -lt "$EFFECTIVE_KEEP_COUNT" ]; then
    EFFECTIVE_KEEP_COUNT="$KEEP_COUNT_MIN"
  fi
  if [ -z "$EFFECTIVE_KEEP_HOURS" ]; then
    EFFECTIVE_KEEP_HOURS="$DISK_GUARD_KEEP_HOURS"
  elif [ "$DISK_GUARD_KEEP_HOURS" -lt "$EFFECTIVE_KEEP_HOURS" ]; then
    EFFECTIVE_KEEP_HOURS="$DISK_GUARD_KEEP_HOURS"
  fi
fi

TMP_REPORT="$(mktemp "$STATE_PACK_DIR/.PRUNE_LAST.json.tmp.XXXXXX")"
python3 - "$STATE_PACK_DIR" "$LATEST_PATH" "$EFFECTIVE_KEEP_COUNT" "${EFFECTIVE_KEEP_HOURS:-}" "$MODE" "$USED_PCT" "$DISK_THRESHOLD_PCT" "$ALERT_REASON" "$TMP_REPORT" <<'PYEOF'
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

state_pack_dir = Path(sys.argv[1])
latest_path = Path(sys.argv[2]) if sys.argv[2] else None
keep_count = max(0, int(sys.argv[3]))
keep_hours = int(sys.argv[4]) if sys.argv[4] else None
mode = sys.argv[5]
used_pct = int(sys.argv[6])
disk_threshold_pct = int(sys.argv[7])
alert_reason = sys.argv[8]
tmp_report = Path(sys.argv[9])

dirs = [path for path in state_pack_dir.iterdir() if path.is_dir()]
dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)

keep_names = {path.name for path in dirs[:keep_count]}
if latest_path is not None and latest_path.exists():
    keep_names.add(latest_path.name)

age_cutoff = None
if keep_hours is not None:
    age_cutoff = datetime.now(timezone.utc) - timedelta(hours=keep_hours)

deleted = []
kept = []
for path in dirs:
    is_latest = latest_path is not None and path.resolve() == latest_path.resolve()
    path_mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    old_by_age = age_cutoff is not None and path_mtime < age_cutoff
    keep_by_count = path.name in keep_names
    should_delete = not is_latest and (not keep_by_count or old_by_age)
    if should_delete:
        shutil.rmtree(path)
        deleted.append(path.name)
    else:
        kept.append(path.name)

report = {
    "status": "PASS",
    "reason": alert_reason,
    "mode": mode,
    "used_pct": used_pct,
    "disk_threshold_pct": disk_threshold_pct,
    "keep_count": keep_count,
    "keep_hours": keep_hours,
    "latest_path": str(latest_path) if latest_path is not None else None,
    "latest_preserved": latest_path is None or latest_path.name in kept,
    "deleted_count": len(deleted),
    "kept_count": len(kept),
    "deleted_dirs": deleted[:10],
    "kept_dirs": kept[:10],
    "state_pack_dir": str(state_pack_dir),
}
tmp_report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, separators=(",", ":")))
PYEOF
mv "$TMP_REPORT" "$PRUNE_REPORT"

#!/usr/bin/env bash
# check_deploy_drift.sh — Fail if repo has unexpected modified files (SCP/out-of-band drift).
#
# Run after deploy. git status --porcelain; exclude allowed paths (project_state, etc).
# Exit 0 if clean or only allowed drift; 1 if unexpected modifications.
# Writes incident to artifacts/incidents/ if drift detected.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

# Paths that deploy is allowed to modify (project state, templates from update_project_state.py)
is_allowed() {
  case "$1" in
    config/project_state.json|docs/OPENCLAW_CURRENT.md|docs/OPENCLAW_NEXT.md) return 0 ;;
    *) return 1 ;;
  esac
}

# Modified tracked files (diff from HEAD)
DRIFT_FILES=""
while IFS= read -r path; do
  [ -z "$path" ] && continue
  is_allowed "$path" && continue
  DRIFT_FILES="${DRIFT_FILES}${path}"$'\n'
done <<< "$(git diff --name-only HEAD 2>/dev/null || true)"

if [ -z "$DRIFT_FILES" ]; then
  echo '{"drift":false,"drift_status":"ok"}'
  exit 0
fi

# Drift detected — write incident
INC_ID="incident_drift_$(date -u +%Y%m%dT%H%M%SZ)"
INC_DIR="$ROOT_DIR/artifacts/incidents/$INC_ID"
mkdir -p "$INC_DIR"
echo "$DRIFT_FILES" | head -50 > "$INC_DIR/drift_files.txt"
cat > "$INC_DIR/SUMMARY.md" << EOF
# Incident: $INC_ID

**Status:** DRIFT_DETECTED
**Timestamp:** $(date -u +%Y-%m-%dT%H:%M:%SZ)

## Summary
Unexpected modified files in /opt/ai-ops-runner after deploy (possible SCP/out-of-band changes).
Production must match origin/main. Run: git reset --hard origin/main
EOF

echo "{\"drift\":true,\"drift_status\":\"unexpected_modifications\",\"incident_id\":\"$INC_ID\",\"files\":$(echo "$DRIFT_FILES" | grep -v '^$' | python3 -c 'import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')}" > "$INC_DIR/drift.json"
echo '{"drift":true,"drift_status":"unexpected_modifications","incident_id":"'"$INC_ID"'"}' >&2
exit 1

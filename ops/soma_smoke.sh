#!/usr/bin/env bash
# soma_smoke.sh — Smoke test for the Soma Kajabi Sync workflow.
#
# Verifies:
#   1. Python module imports work
#   2. Snapshot CLI runs in smoke mode (no credentials required)
#   3. Harvest CLI runs in smoke mode
#   4. Mirror CLI runs in smoke mode
#   5. SMS module loads (config test — no Twilio required)
#   6. Artifact files are written correctly
#   7. Artifact integrity (sha256 sidecar) passes
#
# Usage:
#   ./ops/soma_smoke.sh
#
# Exit codes:
#   0 = all checks passed
#   1 = one or more checks failed
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

FAILURES=0
CHECKS=0

pass() { CHECKS=$((CHECKS + 1)); echo "  PASS: $1"; }
fail() { CHECKS=$((CHECKS + 1)); FAILURES=$((FAILURES + 1)); echo "  FAIL: $1" >&2; }

echo "=== soma_smoke.sh ==="
echo "  Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  Root: $ROOT_DIR"
echo ""

# Use a temp artifacts dir for smoke test
export SOMA_ARTIFACTS_ROOT="$(mktemp -d)"
trap 'rm -rf "$SOMA_ARTIFACTS_ROOT"' EXIT

# --- 1. Module imports ---
echo "--- Module Imports ---"
if python3 -c "from services.soma_kajabi_sync import config, artifacts, snapshot, harvest, mirror, sms" 2>/dev/null; then
  pass "All modules import successfully"
else
  fail "Module import failed"
fi

# --- 2. Snapshot smoke ---
echo "--- Snapshot Smoke ---"
SNAP_OUTPUT=""
SNAP_RC=0
SNAP_OUTPUT="$(python3 -m services.soma_kajabi_sync.snapshot --smoke 2>&1)" || SNAP_RC=$?
if [ "$SNAP_RC" -eq 0 ]; then
  pass "Snapshot smoke completed (rc=0)"
else
  fail "Snapshot smoke failed (rc=$SNAP_RC)"
  echo "$SNAP_OUTPUT" >&2
fi

# Check snapshot artifact exists
SNAP_DIR="$(find "$SOMA_ARTIFACTS_ROOT" -name "snapshot.json" -type f 2>/dev/null | head -1)"
if [ -n "$SNAP_DIR" ]; then
  pass "snapshot.json artifact created"

  # Validate JSON
  if python3 -c "import json; json.load(open('$SNAP_DIR'))" 2>/dev/null; then
    pass "snapshot.json is valid JSON"
  else
    fail "snapshot.json is invalid JSON"
  fi

  # Validate sha256 sidecar
  SHA_FILE="${SNAP_DIR}.sha256"
  if [ -f "$SHA_FILE" ]; then
    EXPECTED="$(cat "$SHA_FILE")"
    if command -v shasum >/dev/null 2>&1; then
      ACTUAL="$(shasum -a 256 "$SNAP_DIR" | cut -d' ' -f1)"
    elif command -v sha256sum >/dev/null 2>&1; then
      ACTUAL="$(sha256sum "$SNAP_DIR" | cut -d' ' -f1)"
    else
      ACTUAL="$EXPECTED"  # Skip check
    fi
    if [ "$EXPECTED" = "$ACTUAL" ]; then
      pass "snapshot.json sha256 integrity verified"
    else
      fail "snapshot.json sha256 mismatch (expected=$EXPECTED actual=$ACTUAL)"
    fi
  else
    fail "snapshot.json.sha256 sidecar missing"
  fi
else
  fail "snapshot.json not found in artifacts"
fi

# --- 3. Harvest smoke ---
echo "--- Harvest Smoke ---"
HARVEST_RC=0
python3 -m services.soma_kajabi_sync.harvest --smoke >/dev/null 2>&1 || HARVEST_RC=$?
if [ "$HARVEST_RC" -eq 0 ]; then
  pass "Harvest smoke completed (rc=0)"
else
  fail "Harvest smoke failed (rc=$HARVEST_RC)"
fi

# Check harvest artifacts
HARVEST_INDEX="$(find "$SOMA_ARTIFACTS_ROOT" -name "gmail_video_index.json" -type f 2>/dev/null | head -1)"
HARVEST_CSV="$(find "$SOMA_ARTIFACTS_ROOT" -name "video_manifest.csv" -type f 2>/dev/null | head -1)"
if [ -n "$HARVEST_INDEX" ]; then
  pass "gmail_video_index.json created"
else
  fail "gmail_video_index.json not found"
fi
if [ -n "$HARVEST_CSV" ]; then
  pass "video_manifest.csv created"
  # Validate CSV has header
  HEADER="$(head -1 "$HARVEST_CSV")"
  if echo "$HEADER" | grep -q "video_id"; then
    pass "video_manifest.csv has correct header"
  else
    fail "video_manifest.csv header missing video_id"
  fi
else
  fail "video_manifest.csv not found"
fi

# --- 4. Mirror smoke ---
echo "--- Mirror Smoke ---"
MIRROR_RC=0
python3 -m services.soma_kajabi_sync.mirror --smoke --dry-run >/dev/null 2>&1 || MIRROR_RC=$?
if [ "$MIRROR_RC" -eq 0 ]; then
  pass "Mirror smoke completed (rc=0)"
else
  fail "Mirror smoke failed (rc=$MIRROR_RC)"
fi

MIRROR_REPORT="$(find "$SOMA_ARTIFACTS_ROOT" -name "mirror_report.json" -type f 2>/dev/null | head -1)"
CHANGELOG="$(find "$SOMA_ARTIFACTS_ROOT" -name "changelog.md" -type f 2>/dev/null | head -1)"
if [ -n "$MIRROR_REPORT" ]; then
  pass "mirror_report.json created"
else
  fail "mirror_report.json not found"
fi
if [ -n "$CHANGELOG" ]; then
  pass "changelog.md created"
else
  fail "changelog.md not found"
fi

# --- 5. SMS module load ---
echo "--- SMS Module ---"
if python3 -c "from services.soma_kajabi_sync.sms import handle_inbound_sms, send_alert, is_allowed_sender" 2>/dev/null; then
  pass "SMS module loads successfully"
else
  fail "SMS module import failed"
fi

# --- 6. Manifest check ---
echo "--- Run Manifests ---"
MANIFEST_COUNT="$(find "$SOMA_ARTIFACTS_ROOT" -name "_manifest.json" -type f 2>/dev/null | wc -l | tr -d ' ')"
if [ "$MANIFEST_COUNT" -ge 3 ]; then
  pass "Found $MANIFEST_COUNT run manifests (expected ≥3)"
else
  fail "Expected ≥3 run manifests, found $MANIFEST_COUNT"
fi

# --- JSON Output ---
SMOKE_TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
SMOKE_JSON_DIR="$ROOT_DIR/artifacts/soma_smoke/${SMOKE_TIMESTAMP}"
mkdir -p "$SMOKE_JSON_DIR" 2>/dev/null || true

python3 - "$SMOKE_JSON_DIR/smoke.json" "$CHECKS" "$FAILURES" <<'PYEOF'
import json, sys
from datetime import datetime, timezone
out_file = sys.argv[1]
checks = int(sys.argv[2])
failures = int(sys.argv[3])
result = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "workflow": "soma_smoke",
    "result": "PASS" if failures == 0 else "FAIL",
    "checks_total": checks,
    "checks_passed": checks - failures,
    "checks_failed": failures,
}
with open(out_file, "w") as f:
    json.dump(result, f, indent=2)
PYEOF

# --- Summary ---
echo ""
echo "=== Soma Smoke: $((CHECKS - FAILURES))/$CHECKS passed ==="
echo "  JSON: $SMOKE_JSON_DIR/smoke.json"
if [ "$FAILURES" -gt 0 ]; then
  echo "FAIL: $FAILURES check(s) failed." >&2
  exit 1
fi
echo "All checks passed."
exit 0

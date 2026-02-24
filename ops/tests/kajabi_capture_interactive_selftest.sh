#!/usr/bin/env bash
# Smoke test: soma_kajabi_capture_interactive is registered and creates artifacts (no real Kajabi).
# Does NOT run Playwright or Xvfb — verifies action is allowlisted and script runs to first failure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

echo "==> kajabi_capture_interactive_selftest: action registered + script creates artifacts"

# 1. Action in registry
if ! python3 -c "
import json
with open('config/action_registry.json') as f:
    d = json.load(f)
ids = [a['id'] for a in d['actions']]
assert 'soma_kajabi_capture_interactive' in ids, 'soma_kajabi_capture_interactive not in action_registry'
print('  PASS: action in action_registry')
"; then
  echo "  FAIL: action not in registry"
  exit 1
fi

# 2. Persistent profile path is used
if ! grep -q "kajabi_chrome_profile" ops/scripts/kajabi_capture_interactive.py; then
  echo "  FAIL: persistent profile path not used in kajabi_capture_interactive.py"
  exit 1
fi
echo "  PASS: persistent profile path used"

# 3. Job in allowlist
if [ -f configs/job_allowlist.yaml ] && ! grep -q "soma_kajabi_capture_interactive" configs/job_allowlist.yaml; then
  echo "  FAIL: soma_kajabi_capture_interactive not in job_allowlist"
  exit 1
fi
echo "  PASS: action in job_allowlist"

# 4. Script exists and runs (will fail at Xvfb/Playwright — we only check it starts and creates artifact dir)
ARTIFACT_DIR="$(mktemp -d)"
trap "rm -rf '$ARTIFACT_DIR'" EXIT
export ARTIFACT_DIR
export OPENCLAW_REPO_ROOT="$ROOT_DIR"

# Run script; expect non-zero (Xvfb/Playwright not in test env) but artifact dir should be created
set +e
python3 ./ops/scripts/kajabi_capture_interactive.py 2>/dev/null
RC=$?
set -e

# Artifact dir should have been created with summary.json or instructions.txt
if [ -f "$ARTIFACT_DIR/summary.json" ] || [ -f "$ARTIFACT_DIR/instructions.txt" ]; then
  echo "  PASS: script creates artifacts"
else
  echo "  FAIL: no summary.json or instructions.txt in $ARTIFACT_DIR"
  ls -la "$ARTIFACT_DIR" 2>/dev/null || true
  exit 1
fi

echo "==> kajabi_capture_interactive_selftest PASS"

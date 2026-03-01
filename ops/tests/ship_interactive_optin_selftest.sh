#!/usr/bin/env bash
# ship_interactive_optin_selftest.sh — Asserts ship sets OPENCLAW_RUN_INTERACTIVE_TESTS=0 by default
# and capture_interactive fails closed (exit 1) when human gate is disabled.
# Interactive/noVNC selftests must be opt-in only; ship must be hermetic on Mac (no noVNC backend).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SHIP="$ROOT_DIR/ops/ship.sh"

echo "==> ship_interactive_optin_selftest"

# 1. ship.sh exports OPENCLAW_RUN_INTERACTIVE_TESTS=0 by default
grep -q 'OPENCLAW_RUN_INTERACTIVE_TESTS' "$SHIP" || { echo "  FAIL: ship.sh must set OPENCLAW_RUN_INTERACTIVE_TESTS" >&2; exit 1; }
grep -qE 'OPENCLAW_RUN_INTERACTIVE_TESTS.*:-0' "$SHIP" || { echo "  FAIL: ship.sh must default OPENCLAW_RUN_INTERACTIVE_TESTS to 0" >&2; exit 1; }
echo "  PASS: ship.sh exports OPENCLAW_RUN_INTERACTIVE_TESTS=0 by default"

# 1b. ship.sh exports OPENCLAW_ENABLE_HUMAN_GATE=0 by default
grep -q 'OPENCLAW_ENABLE_HUMAN_GATE' "$SHIP" || { echo "  FAIL: ship.sh must set OPENCLAW_ENABLE_HUMAN_GATE" >&2; exit 1; }
grep -qE 'OPENCLAW_ENABLE_HUMAN_GATE.*:-0' "$SHIP" || { echo "  FAIL: ship.sh must default OPENCLAW_ENABLE_HUMAN_GATE to 0" >&2; exit 1; }
echo "  PASS: ship.sh exports OPENCLAW_ENABLE_HUMAN_GATE=0 by default"

# 2. capture_interactive fails closed (exit 1) when human gate disabled
ARTIFACT_DIR="$(mktemp -d)"
trap "rm -rf '$ARTIFACT_DIR'" EXIT
export ARTIFACT_DIR
export OPENCLAW_REPO_ROOT="$ROOT_DIR"
export OPENCLAW_RUN_INTERACTIVE_TESTS=0
export OPENCLAW_ENABLE_HUMAN_GATE=0

python3 "$ROOT_DIR/ops/scripts/kajabi_capture_interactive.py" 2>/dev/null && { echo "  FAIL: capture_interactive must exit non-zero when human gate disabled" >&2; exit 1; } || true
[ -f "$ARTIFACT_DIR/WAITING_FOR_HUMAN.json" ] || { echo "  FAIL: WAITING_FOR_HUMAN.json must exist when gate disabled" >&2; exit 1; }
python3 -c "
import json, sys
wfh = json.loads(open('$ARTIFACT_DIR/WAITING_FOR_HUMAN.json').read())
assert wfh.get('error_class') == 'INTERACTIVE_DISABLED', f'expected INTERACTIVE_DISABLED, got {wfh.get(\"error_class\")}'
assert 'remediation' in wfh, 'WAITING_FOR_HUMAN.json must include remediation'
print('  validated: error_class=INTERACTIVE_DISABLED, remediation present')
" || { echo "  FAIL: WAITING_FOR_HUMAN.json contract violated" >&2; exit 1; }
echo "  PASS: capture_interactive fails closed with WAITING_FOR_HUMAN.json when gate disabled"

echo "==> ship_interactive_optin_selftest PASS"

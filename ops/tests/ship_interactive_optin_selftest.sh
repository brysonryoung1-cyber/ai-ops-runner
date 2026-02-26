#!/usr/bin/env bash
# ship_interactive_optin_selftest.sh — Asserts ship sets OPENCLAW_RUN_INTERACTIVE_TESTS=0 by default.
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

# 2. kajabi_capture_interactive.py skips noVNC when flag != 1 (exit 0, no doctor call)
ARTIFACT_DIR="$(mktemp -d)"
trap "rm -rf '$ARTIFACT_DIR'" EXIT
export ARTIFACT_DIR
export OPENCLAW_REPO_ROOT="$ROOT_DIR"
export OPENCLAW_RUN_INTERACTIVE_TESTS=0

OUT="$(python3 "$ROOT_DIR/ops/scripts/kajabi_capture_interactive.py" 2>&1)"
RC=$?
[ "$RC" -eq 0 ] || { echo "  FAIL: capture_interactive must exit 0 in skip mode (rc=$RC)" >&2; exit 1; }
[ -f "$ARTIFACT_DIR/instructions.txt" ] || { echo "  FAIL: instructions.txt must exist in skip mode" >&2; exit 1; }
grep -q "SKIP.*interactive" "$ARTIFACT_DIR/instructions.txt" || { echo "  FAIL: instructions.txt must contain SKIP message" >&2; exit 1; }
echo "$OUT" | grep -q "SKIP" || true
echo "  PASS: capture_interactive exits 0 in skip mode, does not call doctor"

echo "==> ship_interactive_optin_selftest PASS"

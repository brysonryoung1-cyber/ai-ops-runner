#!/usr/bin/env bash
# kajabi_ui_ensure_selftest.sh — Selftest: kajabi_ui_ensure.sh exists, references DISPLAY from config, uses profile path.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

echo "==> kajabi_ui_ensure_selftest"

ENSURE="$ROOT_DIR/ops/scripts/kajabi_ui_ensure.sh"
[ -f "$ENSURE" ] || { echo "FAIL: kajabi_ui_ensure.sh not found" >&2; exit 1; }
[ -x "$ENSURE" ] || { echo "FAIL: kajabi_ui_ensure.sh not executable" >&2; exit 1; }

grep -q 'novnc_display.env' "$ENSURE" || { echo "FAIL: kajabi_ui_ensure.sh must reference novnc_display.env for DISPLAY" >&2; exit 1; }
grep -q 'kajabi_chrome_profile' "$ENSURE" || { echo "FAIL: kajabi_ui_ensure.sh must use kajabi_chrome_profile path" >&2; exit 1; }
grep -q 'app.kajabi.com' "$ENSURE" || { echo "FAIL: kajabi_ui_ensure.sh must open Kajabi URL" >&2; exit 1; }
grep -q 'DISPLAY' "$ENSURE" || { echo "FAIL: kajabi_ui_ensure.sh must reference DISPLAY" >&2; exit 1; }

# Config template exists
[ -f "$ROOT_DIR/config/novnc_display.env" ] || { echo "FAIL: config/novnc_display.env not found" >&2; exit 1; }
grep -q 'DISPLAY=' "$ROOT_DIR/config/novnc_display.env" || { echo "FAIL: config/novnc_display.env must define DISPLAY" >&2; exit 1; }

echo "==> kajabi_ui_ensure_selftest PASS"

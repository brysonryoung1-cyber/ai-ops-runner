#!/usr/bin/env bash
# Selftest: aiops_soma_run_to_done.sh syntax + --help (no VPS contact).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

echo "==> aiops_soma_run_to_done_selftest"

# 1. Syntax check (bash -n)
bash -n "$ROOT_DIR/ops/remote/aiops_soma_run_to_done.sh" || { echo "  FAIL: bash -n syntax check"; exit 1; }
echo "  PASS: bash -n syntax check"

# 2. --help exits 0, no VPS contact
"$ROOT_DIR/ops/remote/aiops_soma_run_to_done.sh" --help >/dev/null 2>&1 || { echo "  FAIL: --help"; exit 1; }
echo "  PASS: --help"

echo "==> aiops_soma_run_to_done_selftest PASS"

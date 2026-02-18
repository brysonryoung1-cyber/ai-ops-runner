#!/usr/bin/env bash
# bootstrap_branch_protection.sh — One-time setup for verdict-gate branch protection
#
# GitHub will not let you add a required status check until that check has run
# at least once. This script ensures the workflow exists and gives you the
# exact steps to enable protection without ever clearing required checks.
#
# INVARIANT: We never remove or clear required_status_checks contexts.
# Only add "verdict-gate" (or set initial protection with just verdict-gate).
#
# Usage: ./ops/bootstrap_branch_protection.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

WORKFLOW_FILE=".github/workflows/verdict_gate.yml"
CHECK_NAME="verdict-gate"

echo "=== Bootstrap: verdict-gate branch protection ==="
echo ""

# 1) Workflow must exist in repo
if [ ! -f "$ROOT_DIR/$WORKFLOW_FILE" ]; then
  echo "ERROR: $WORKFLOW_FILE not found. Add the verdict gate workflow to the repo first." >&2
  exit 1
fi
echo "  [OK] $WORKFLOW_FILE exists."
echo ""

# 2) Instruct: run workflow once so the check name exists
echo "--- Step 1: Create the check name ---"
echo "  The required status check 'verdict-gate' must exist before you can add it to branch protection."
echo "  Do ONE of:"
echo "    • Push a commit to main (e.g. from this repo) and wait for the workflow to run, or"
echo "    • GitHub → Actions → Verdict Gate → Run workflow."
echo "  Wait until the run completes so that '$CHECK_NAME' appears in the branch protection status list."
echo ""

# 3) Get owner/repo for gh commands
ORIGIN_URL="$(git remote get-url origin 2>/dev/null || true)"
if [ -z "$ORIGIN_URL" ]; then
  echo "  (No git origin; use OWNER/REPO in the commands below.)"
  OWNER_REPO="OWNER/REPO"
else
  if echo "$ORIGIN_URL" | grep -q '^https://github.com/'; then
    OWNER_REPO="$(echo "$ORIGIN_URL" | sed -n 's|https://github.com/\([^/]*\)/\([^.]*\)\.git|\1/\2|p')"
  elif echo "$ORIGIN_URL" | grep -q '^git@github.com:'; then
    OWNER_REPO="$(echo "$ORIGIN_URL" | sed -n 's|git@github.com:\([^/]*\)/\([^.]*\)\.git|\1/\2|p')"
  else
    OWNER_REPO="OWNER/REPO"
  fi
fi

echo "--- Step 2: Add verdict-gate to branch protection ---"
echo "  Option A — Add context only (branch protection already exists with other checks):"
echo "    gh api -X POST -H 'Accept: application/vnd.github+json' \\"
echo "      /repos/$OWNER_REPO/branches/main/protection/required_status_checks/contexts \\"
echo "      -f 'contexts[]=verdict-gate'"
echo ""
echo "  Option B — Via GitHub UI (no API):"
echo "    Settings → Branches → Branch protection rules → main → Edit"
echo "    Enable 'Require status checks to pass before merging'"
echo "    In 'Status checks that are required', search and add: verdict-gate"
echo "    Enable 'Do not allow bypassing the above settings'"
echo "    Save."
echo ""
echo "  IMPORTANT: Never clear or remove required status checks to bypass the gate."
echo "  Run ./ops/doctor_repo.sh after setup to verify."
echo ""
echo "=== Bootstrap instructions complete ==="

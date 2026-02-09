#!/usr/bin/env bash
# orb_doctor.sh — Run ORB repo's doctor script in read-only worktree.
# Called by executor with cwd = worktree.
# Env vars (set by executor): ARTIFACT_DIR
set -euo pipefail

OUT="$ARTIFACT_DIR/DOCTOR_OUTPUT.txt"

echo "==> orb_doctor"
echo "    cwd=$(pwd)"
echo "    artifact_dir=$ARTIFACT_DIR"

if [ -f ./ops/doctor_repo.sh ]; then
  echo "==> Running ./ops/doctor_repo.sh"
  bash ./ops/doctor_repo.sh > "$OUT" 2>&1 || {
    RC=$?
    echo "==> doctor_repo.sh exited with code $RC"
    echo "--- EXIT CODE: $RC ---" >> "$OUT"
    exit $RC
  }
  echo "==> DOCTOR_OUTPUT.txt written ($(wc -c < "$OUT" | tr -d ' ') bytes)"
elif [ -f ./ops/doctor_repo.ps1 ]; then
  echo "PowerShell script found but not supported in this runner environment" >&2
  echo "UNSUPPORTED: ./ops/doctor_repo.ps1 (PowerShell not available in runner)" > "$OUT"
  exit 1
else
  echo "SCRIPT_NOT_FOUND: No doctor_repo script found in target repo" >&2
  echo "SCRIPT_NOT_FOUND: ./ops/doctor_repo.sh" > "$OUT"
  exit 1
fi
